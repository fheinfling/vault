import Foundation

/// Which incremental stream a cursor belongs to.
public enum SyncStream: String, Sendable, CaseIterable {
    case transactions
    case activities
}

/// What `upsertAccounts` reports back: every account's row ID, and which ones are new.
public struct AccountUpsertResult: Sendable, Equatable {
    public var ids: [String: Int64]
    public var newExternalIDs: [String]
}

/// The single writer to the vault database. All connectors' records go through here.
public actor Ledger {
    private let db: Database

    public init(path: String) throws {
        if path != ":memory:" {
            // A bare file name has no directory component; creating "" would fail.
            let directory = (path as NSString).deletingLastPathComponent
            if !directory.isEmpty {
                try FileManager.default.createDirectory(
                    atPath: directory, withIntermediateDirectories: true,
                    attributes: [.posixPermissions: 0o700]
                )
            }
        }
        db = try Database(path: path)
        try Schema.migrate(db)
    }

    // MARK: - Connections

    public func upsertConnection(id: String, kind: ConnectionKind) throws {
        try db.run(
            """
            INSERT INTO connection (id, kind) VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET kind = excluded.kind
            """,
            [.text(id), .text(kind.rawValue)]
        )
    }

    public func setNeedsUserAction(connectionID: String, message: String?) throws {
        try db.run(
            "UPDATE connection SET needs_user_action = ? WHERE id = ?",
            [message.map { .text($0) } ?? .null, .text(connectionID)]
        )
    }

    public func markSynced(connectionID: String, at: Date = Date()) throws {
        try db.run(
            "UPDATE connection SET last_sync = ?, needs_user_action = NULL WHERE id = ?",
            [.text(Self.iso(at)), .text(connectionID)]
        )
    }

    // MARK: - Accounts

    /// Inserts or updates accounts. Reports every account's row ID and which external IDs were new.
    ///
    /// When an account is new, the same transaction deletes the connection's sync cursors. That way the
    /// full-history fetch a new account is owed is stored with the account itself: every run fetches from
    /// the start until one succeeds end to end and sets the cursors again, even if this run fails later.
    @discardableResult
    public func upsertAccounts(connectionID: String, _ records: [AccountRecord]) throws -> AccountUpsertResult {
        try db.transaction {
            let before = try accountIDs(connectionID: connectionID)
            for r in records {
                try db.run(
                    """
                    INSERT INTO account
                        (connection_id, external_id, provider, kind, name, iban_masked, currency, is_portfolio, hidden)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(connection_id, external_id) DO UPDATE SET
                        provider = excluded.provider,
                        kind = excluded.kind,
                        name = excluded.name,
                        iban_masked = excluded.iban_masked,
                        currency = excluded.currency,
                        is_portfolio = excluded.is_portfolio
                    """,
                    [
                        .text(connectionID), .text(r.externalID), .text(r.provider), .text(r.kind.rawValue),
                        .text(r.name), r.ibanMasked.map { .text($0) } ?? .null, .text(r.currency),
                        .integer(r.isPortfolio ? 1 : 0), .integer(r.hidden ? 1 : 0),
                    ]
                )
            }
            // Second pass: parents, once every account of this batch exists.
            for r in records where r.parentExternalID != nil {
                try db.run(
                    """
                    UPDATE account SET parent_id =
                        (SELECT id FROM account WHERE connection_id = ? AND external_id = ?)
                    WHERE connection_id = ? AND external_id = ?
                    """,
                    [.text(connectionID), .text(r.parentExternalID!), .text(connectionID), .text(r.externalID)]
                )
            }
            let after = try accountIDs(connectionID: connectionID)
            let new = records.map(\.externalID).filter { before[$0] == nil && after[$0] != nil }
            if !new.isEmpty {
                try db.run("DELETE FROM sync_cursor WHERE connection_id = ?", [.text(connectionID)])
            }
            return AccountUpsertResult(ids: after, newExternalIDs: new)
        }
    }

    public func accountIDs(connectionID: String) throws -> [String: Int64] {
        let rows = try db.query(
            "SELECT id, external_id FROM account WHERE connection_id = ?", [.text(connectionID)]
        )
        var map: [String: Int64] = [:]
        for row in rows {
            if let id = row["id"]?.int64, let ext = row["external_id"]?.string { map[ext] = id }
        }
        return map
    }

    public func accounts(includeHidden: Bool = false) throws -> [AccountSummary] {
        let rows = try db.query(
            """
            SELECT a.id, a.connection_id, a.external_id, a.provider, a.kind, a.name, a.currency,
                   a.is_portfolio, a.hidden, b.amount AS balance, b.at AS balance_at
            FROM account a
            LEFT JOIN balance_snapshot b ON b.id = (
                SELECT id FROM balance_snapshot WHERE account_id = a.id ORDER BY at DESC, id DESC LIMIT 1
            )
            WHERE (? = 1 OR a.hidden = 0)
            ORDER BY a.provider, a.is_portfolio, a.name
            """,
            [.integer(includeHidden ? 1 : 0)]
        )
        return rows.compactMap { row in
            guard let id = row["id"]?.int64,
                  let kind = row["kind"]?.string.flatMap(AccountKind.init(rawValue:))
            else { return nil }
            return AccountSummary(
                id: id,
                connectionID: row["connection_id"]?.string ?? "",
                externalID: row["external_id"]?.string ?? "",
                provider: row["provider"]?.string ?? "",
                kind: kind,
                name: row["name"]?.string ?? "",
                currency: row["currency"]?.string ?? "",
                isPortfolio: row["is_portfolio"]?.bool ?? false,
                hidden: row["hidden"]?.bool ?? false,
                balance: row["balance"]?.string.flatMap { Decimal(string: $0) },
                balanceAt: row["balance_at"]?.string.flatMap(Self.date)
            )
        }
    }

    // MARK: - Snapshots

    /// Records one balance per account for this run. A second balance for the same account in the same run replaces the first.
    @discardableResult
    public func recordBalances(connectionID: String, runID: Int64, _ records: [BalanceRecord]) throws -> Int {
        let ids = try accountIDs(connectionID: connectionID)
        return try db.transaction {
            var count = 0
            for r in records {
                guard let accountID = ids[r.accountExternalID] else { continue }
                try db.run(
                    """
                    INSERT INTO balance_snapshot (account_id, run_id, at, amount, currency) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, run_id) DO UPDATE SET
                        at = excluded.at, amount = excluded.amount, currency = excluded.currency
                    """,
                    [
                        .integer(accountID), .integer(runID), .text(Self.iso(r.at)),
                        .text(Self.decimal(r.amount)), .text(r.currency),
                    ]
                )
                count += 1
            }
            return count
        }
    }

    public func balanceHistory(accountID: Int64) throws -> [(at: Date, amount: Decimal, currency: String)] {
        let rows = try db.query(
            "SELECT at, amount, currency FROM balance_snapshot WHERE account_id = ? ORDER BY at, id",
            [.integer(accountID)]
        )
        return rows.compactMap { row in
            guard let at = row["at"]?.string.flatMap(Self.date),
                  let amount = row["amount"]?.string.flatMap({ Decimal(string: $0) })
            else { return nil }
            return (at, amount, row["currency"]?.string ?? "")
        }
    }

    /// Records the complete set of holdings this run observed. Every portfolio account of the connection,
    /// and every account the records mention, gets a `holding_set` row, so an emptied depot is stored as
    /// "seen at `at`, no positions" rather than left looking like its last non-empty snapshot.
    @discardableResult
    public func recordHoldings(connectionID: String, runID: Int64, at: Date, _ records: [HoldingRecord]) throws -> Int {
        let ids = try accountIDs(connectionID: connectionID)
        let portfolioIDs = try db.query(
            "SELECT id FROM account WHERE connection_id = ? AND is_portfolio = 1", [.text(connectionID)]
        ).compactMap { $0["id"]?.int64 }
        let mentioned = records.compactMap { ids[$0.accountExternalID] }
        let setAccounts = Set(portfolioIDs + mentioned)
        return try db.transaction {
            for accountID in setAccounts {
                try db.run(
                    """
                    INSERT INTO holding_set (account_id, run_id, at) VALUES (?, ?, ?)
                    ON CONFLICT(account_id, run_id) DO UPDATE SET at = excluded.at
                    """,
                    [.integer(accountID), .integer(runID), .text(Self.iso(at))]
                )
            }
            var count = 0
            for r in records {
                guard let accountID = ids[r.accountExternalID] else { continue }
                try db.run(
                    """
                    INSERT INTO holding_snapshot (account_id, run_id, at, isin, name, quantity, price, value, currency)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        .integer(accountID), .integer(runID), .text(Self.iso(r.at)), r.isin.map { .text($0) } ?? .null,
                        .text(r.name), .text(Self.decimal(r.quantity)),
                        r.price.map { .text(Self.decimal($0)) } ?? .null,
                        r.value.map { .text(Self.decimal($0)) } ?? .null, .text(r.currency),
                    ]
                )
                count += 1
            }
            return count
        }
    }

    /// The positions from the most recent holdings set for this account. Empty if that set was empty.
    /// Rows written before v2 have no set; for those the latest timestamp is used.
    public func latestHoldings(accountID: Int64) throws -> [HoldingRecord] {
        let latestSet = try db.query(
            "SELECT run_id FROM holding_set WHERE account_id = ? ORDER BY id DESC LIMIT 1", [.integer(accountID)]
        ).first?["run_id"]?.int64
        let rows: [[String: SQLValue]]
        if let runID = latestSet {
            rows = try db.query(
                """
                SELECT h.*, a.external_id FROM holding_snapshot h
                JOIN account a ON a.id = h.account_id
                WHERE h.account_id = ? AND h.run_id = ?
                ORDER BY h.name, h.id
                """,
                [.integer(accountID), .integer(runID)]
            )
        } else {
            rows = try db.query(
                """
                SELECT h.*, a.external_id FROM holding_snapshot h
                JOIN account a ON a.id = h.account_id
                WHERE h.account_id = ? AND h.run_id IS NULL
                  AND h.at = (SELECT MAX(at) FROM holding_snapshot WHERE account_id = ? AND run_id IS NULL)
                ORDER BY h.name, h.id
                """,
                [.integer(accountID), .integer(accountID)]
            )
        }
        return rows.compactMap { row in
            guard let at = row["at"]?.string.flatMap(Self.date),
                  let quantity = row["quantity"]?.string.flatMap({ Decimal(string: $0) })
            else { return nil }
            return HoldingRecord(
                accountExternalID: row["external_id"]?.string ?? "",
                at: at, isin: row["isin"]?.string, name: row["name"]?.string ?? "",
                quantity: quantity,
                price: row["price"]?.string.flatMap { Decimal(string: $0) },
                value: row["value"]?.string.flatMap { Decimal(string: $0) },
                currency: row["currency"]?.string ?? ""
            )
        }
    }

    // MARK: - Transactions and activities

    /// Inserts new transactions and updates existing ones (some providers change amounts after booking).
    @discardableResult
    public func upsertTransactions(connectionID: String, _ records: [TransactionRecord]) throws -> Int {
        let ids = try accountIDs(connectionID: connectionID)
        return try db.transaction {
            var count = 0
            for r in records {
                guard let accountID = ids[r.accountExternalID] else { continue }
                count += try db.run(
                    """
                    INSERT INTO "transaction"
                        (account_id, external_id, booked_at, value_at, amount, currency, counterparty, purpose, category, booked, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, external_id) DO UPDATE SET
                        booked_at = excluded.booked_at,
                        value_at = excluded.value_at,
                        amount = excluded.amount,
                        currency = excluded.currency,
                        counterparty = excluded.counterparty,
                        purpose = excluded.purpose,
                        booked = excluded.booked,
                        raw_json = excluded.raw_json
                    """,
                    [
                        .integer(accountID), .text(r.externalID), .text(Self.iso(r.bookedAt)),
                        r.valueAt.map { .text(Self.iso($0)) } ?? .null, .text(Self.decimal(r.amount)),
                        .text(r.currency), r.counterparty.map { .text($0) } ?? .null,
                        r.purpose.map { .text($0) } ?? .null, r.category.map { .text($0) } ?? .null,
                        .integer(r.booked ? 1 : 0), r.raw.map { .text($0) } ?? .null,
                    ]
                )
            }
            return count
        }
    }

    @discardableResult
    public func upsertActivities(connectionID: String, _ records: [ActivityRecord]) throws -> Int {
        let ids = try accountIDs(connectionID: connectionID)
        return try db.transaction {
            var count = 0
            for r in records {
                guard let accountID = ids[r.accountExternalID] else { continue }
                count += try db.run(
                    """
                    INSERT INTO activity
                        (account_id, external_id, at, type, isin, quantity, price, fee, tax, amount, currency, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, external_id) DO UPDATE SET
                        at = excluded.at, type = excluded.type, isin = excluded.isin,
                        quantity = excluded.quantity, price = excluded.price, fee = excluded.fee,
                        tax = excluded.tax, amount = excluded.amount, currency = excluded.currency,
                        raw_json = excluded.raw_json
                    """,
                    [
                        .integer(accountID), .text(r.externalID), .text(Self.iso(r.at)), .text(r.type.rawValue),
                        r.isin.map { .text($0) } ?? .null,
                        r.quantity.map { .text(Self.decimal($0)) } ?? .null,
                        r.price.map { .text(Self.decimal($0)) } ?? .null,
                        r.fee.map { .text(Self.decimal($0)) } ?? .null,
                        r.tax.map { .text(Self.decimal($0)) } ?? .null,
                        .text(Self.decimal(r.amount)), .text(r.currency), r.raw.map { .text($0) } ?? .null,
                    ]
                )
            }
            return count
        }
    }

    public func transactionCount(connectionID: String) throws -> Int {
        let rows = try db.query(
            """
            SELECT COUNT(*) AS n FROM "transaction" t JOIN account a ON a.id = t.account_id
            WHERE a.connection_id = ?
            """,
            [.text(connectionID)]
        )
        return Int(rows.first?["n"]?.int64 ?? 0)
    }

    public func activityCount(connectionID: String) throws -> Int {
        let rows = try db.query(
            "SELECT COUNT(*) AS n FROM activity x JOIN account a ON a.id = x.account_id WHERE a.connection_id = ?",
            [.text(connectionID)]
        )
        return Int(rows.first?["n"]?.int64 ?? 0)
    }

    /// The most recent booking date this connection has stored. Informational; sync uses `cursor(_:_:)`.
    public func latestBookedAt(connectionID: String) throws -> Date? {
        let rows = try db.query(
            """
            SELECT MAX(t.booked_at) AS latest FROM "transaction" t JOIN account a ON a.id = t.account_id
            WHERE a.connection_id = ?
            """,
            [.text(connectionID)]
        )
        return rows.first?["latest"]?.string.flatMap(Self.date)
    }

    // MARK: - Sync cursors

    /// Where the last fully successful run got to for one stream. `nil` means never synced.
    public func cursor(connectionID: String, stream: SyncStream) throws -> Date? {
        let rows = try db.query(
            "SELECT value FROM sync_cursor WHERE connection_id = ? AND stream = ?",
            [.text(connectionID), .text(stream.rawValue)]
        )
        return rows.first?["value"]?.string.flatMap(Self.date)
    }

    public func setCursor(connectionID: String, stream: SyncStream, value: Date) throws {
        try db.run(
            """
            INSERT INTO sync_cursor (connection_id, stream, value) VALUES (?, ?, ?)
            ON CONFLICT(connection_id, stream) DO UPDATE SET value = excluded.value
            """,
            [.text(connectionID), .text(stream.rawValue), .text(Self.iso(value))]
        )
    }

    // MARK: - Sync runs

    public func startSyncRun(connectionID: String, at: Date = Date()) throws -> Int64 {
        try db.run(
            "INSERT INTO sync_run (connection_id, started) VALUES (?, ?)",
            [.text(connectionID), .text(Self.iso(at))]
        )
        return db.lastInsertRowID
    }

    public func finishSyncRun(id: Int64, ok: Bool, accounts: Int, rows: Int, error: String?, at: Date = Date()) throws {
        try db.run(
            "UPDATE sync_run SET finished = ?, ok = ?, accounts = ?, rows = ?, error = ? WHERE id = ?",
            [
                .text(Self.iso(at)), .integer(ok ? 1 : 0), .integer(Int64(accounts)), .integer(Int64(rows)),
                error.map { .text($0) } ?? .null, .integer(id),
            ]
        )
    }

    public func recentSyncRuns(limit: Int = 20) throws -> [SyncRunSummary] {
        let rows = try db.query(
            "SELECT * FROM sync_run ORDER BY started DESC, id DESC LIMIT ?", [.integer(Int64(limit))]
        )
        return rows.compactMap { row in
            guard let id = row["id"]?.int64, let started = row["started"]?.string.flatMap(Self.date) else { return nil }
            return SyncRunSummary(
                id: id,
                connectionID: row["connection_id"]?.string ?? "",
                started: started,
                finished: row["finished"]?.string.flatMap(Self.date),
                ok: row["ok"]?.int64.map { $0 != 0 },
                accounts: Int(row["accounts"]?.int64 ?? 0),
                rows: Int(row["rows"]?.int64 ?? 0),
                error: row["error"]?.string
            )
        }
    }

    // MARK: - Encoding

    static func iso(_ date: Date) -> String {
        date.formatted(.iso8601)
    }

    static func date(_ string: String) -> Date? {
        try? Date(string, strategy: .iso8601)
    }

    static func decimal(_ value: Decimal) -> String {
        "\(value)"
    }
}
