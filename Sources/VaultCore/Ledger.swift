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
    private let fields: FieldStorage
    /// The pre-encryption schema version this open converted, or nil when there was nothing to convert.
    public nonisolated let convertedLegacyVersion: Int64?

    /// Opens or creates the ledger. A database from before field encryption is converted only with
    /// `legacy: .convert`; otherwise it is left untouched and `LegacyDatabaseError` is thrown.
    public init(path: String, cipher: any FieldCipher, legacy: LegacyConversion = .refuse) throws {
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
        db = try Database(path: path, writer: true)
        fields = FieldStorage(cipher: cipher)
        convertedLegacyVersion = try Schema.migrate(db, fields: fields, legacy: legacy)
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
                        (connection_id, external_id, external_id_sealed, provider, kind, name, iban_masked, currency, is_portfolio, hidden)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(connection_id, external_id) DO UPDATE SET
                        external_id_sealed = excluded.external_id_sealed,
                        provider = excluded.provider,
                        kind = excluded.kind,
                        name = excluded.name,
                        iban_masked = excluded.iban_masked,
                        currency = excluded.currency,
                        is_portfolio = excluded.is_portfolio
                    """,
                    [
                        .text(connectionID), fields.index(r.externalID, "account"),
                        try fields.seal(r.externalID, "account.external_id_sealed"),
                        try fields.seal(r.provider, "account.provider"), .text(r.kind.rawValue),
                        try fields.seal(r.name, "account.name"), try fields.seal(r.ibanMasked, "account.iban_masked"), .text(r.currency),
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
                    [.text(connectionID), fields.index(r.parentExternalID!, "account"), .text(connectionID), fields.index(r.externalID, "account")]
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
            "SELECT id, external_id_sealed FROM account WHERE connection_id = ?", [.text(connectionID)]
        )
        var map: [String: Int64] = [:]
        for row in rows {
            if let id = row["id"]?.int64, let ext = try fields.open(row["external_id_sealed"], "account.external_id_sealed") { map[ext] = id }
        }
        return map
    }

    public func accounts(includeHidden: Bool = false) throws -> [AccountSummary] {
        let rows = try db.query(
            """
            SELECT a.id, a.connection_id, a.external_id_sealed, a.provider, a.kind, a.name, a.currency,
                   a.is_portfolio, a.hidden, b.amount AS balance, b.at AS balance_at
            FROM account a
            LEFT JOIN balance_snapshot b ON b.id = (
                SELECT id FROM balance_snapshot WHERE account_id = a.id ORDER BY at DESC, id DESC LIMIT 1
            )
            WHERE (? = 1 OR a.hidden = 0)
            """,
            [.integer(includeHidden ? 1 : 0)]
        )
        return try rows.compactMap { row in
            guard let id = row["id"]?.int64,
                  let kind = row["kind"]?.string.flatMap(AccountKind.init(rawValue:))
            else { return nil }
            return AccountSummary(
                id: id,
                connectionID: row["connection_id"]?.string ?? "",
                externalID: try fields.open(row["external_id_sealed"], "account.external_id_sealed") ?? "",
                provider: try fields.open(row["provider"], "account.provider") ?? "",
                kind: kind,
                name: try fields.open(row["name"], "account.name") ?? "",
                currency: row["currency"]?.string ?? "",
                isPortfolio: row["is_portfolio"]?.bool ?? false,
                hidden: row["hidden"]?.bool ?? false,
                balance: try fields.open(row["balance"], "balance_snapshot.amount").flatMap { Decimal(string: $0) },
                balanceAt: row["balance_at"]?.string.flatMap(Self.date)
            )
        }.sorted {
            if $0.provider != $1.provider { return $0.provider < $1.provider }
            if $0.isPortfolio != $1.isPortfolio { return !$0.isPortfolio }
            return $0.name < $1.name
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
                        try fields.seal(Self.decimal(r.amount), "balance_snapshot.amount"), .text(r.currency),
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
        return try rows.compactMap { row in
            guard let at = row["at"]?.string.flatMap(Self.date),
                  let amount = try fields.open(row["amount"], "balance_snapshot.amount").flatMap({ Decimal(string: $0) })
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
                        .integer(accountID), .integer(runID), .text(Self.iso(r.at)), try fields.seal(r.isin, "holding_snapshot.isin"),
                        try fields.seal(r.name, "holding_snapshot.name"), try fields.seal(Self.decimal(r.quantity), "holding_snapshot.quantity"),
                        try fields.seal(r.price.map(Self.decimal), "holding_snapshot.price"),
                        try fields.seal(r.value.map(Self.decimal), "holding_snapshot.value"), .text(r.currency),
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
                SELECT h.*, a.external_id_sealed FROM holding_snapshot h
                JOIN account a ON a.id = h.account_id
                WHERE h.account_id = ? AND h.run_id = ?
                ORDER BY h.id
                """,
                [.integer(accountID), .integer(runID)]
            )
        } else {
            rows = try db.query(
                """
                SELECT h.*, a.external_id_sealed FROM holding_snapshot h
                JOIN account a ON a.id = h.account_id
                WHERE h.account_id = ? AND h.run_id IS NULL
                  AND h.at = (SELECT MAX(at) FROM holding_snapshot WHERE account_id = ? AND run_id IS NULL)
                ORDER BY h.id
                """,
                [.integer(accountID), .integer(accountID)]
            )
        }
        return try rows.compactMap { row in
            guard let at = row["at"]?.string.flatMap(Self.date),
                  let quantity = try fields.open(row["quantity"], "holding_snapshot.quantity").flatMap({ Decimal(string: $0) })
            else { return nil }
            return HoldingRecord(
                accountExternalID: try fields.open(row["external_id_sealed"], "account.external_id_sealed") ?? "",
                at: at, isin: try fields.open(row["isin"], "holding_snapshot.isin"),
                name: try fields.open(row["name"], "holding_snapshot.name") ?? "",
                quantity: quantity,
                price: try fields.open(row["price"], "holding_snapshot.price").flatMap { Decimal(string: $0) },
                value: try fields.open(row["value"], "holding_snapshot.value").flatMap { Decimal(string: $0) },
                currency: row["currency"]?.string ?? ""
            )
        }.sorted { $0.name < $1.name }
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
                        (account_id, external_id, external_id_sealed, booked_at, value_at, amount, currency, counterparty, purpose, category, booked, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, external_id) DO UPDATE SET
                        external_id_sealed = excluded.external_id_sealed,
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
                        .integer(accountID), fields.index(r.externalID, "transaction"),
                        try fields.seal(r.externalID, "transaction.external_id_sealed"), .text(Self.iso(r.bookedAt)),
                        r.valueAt.map { .text(Self.iso($0)) } ?? .null, try fields.seal(Self.decimal(r.amount), "transaction.amount"),
                        .text(r.currency), try fields.seal(r.counterparty, "transaction.counterparty"),
                        try fields.seal(r.purpose, "transaction.purpose"), try fields.seal(r.category, "transaction.category"),
                        .integer(r.booked ? 1 : 0), try fields.seal(r.raw, "transaction.raw_json"),
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
                        (account_id, external_id, external_id_sealed, at, type, isin, quantity, price, fee, tax, amount, currency, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, external_id) DO UPDATE SET
                        external_id_sealed = excluded.external_id_sealed,
                        at = excluded.at, type = excluded.type, isin = excluded.isin,
                        quantity = excluded.quantity, price = excluded.price, fee = excluded.fee,
                        tax = excluded.tax, amount = excluded.amount, currency = excluded.currency,
                        raw_json = excluded.raw_json
                    """,
                    [
                        .integer(accountID), fields.index(r.externalID, "activity"),
                        try fields.seal(r.externalID, "activity.external_id_sealed"), .text(Self.iso(r.at)), .text(r.type.rawValue),
                        try fields.seal(r.isin, "activity.isin"),
                        try fields.seal(r.quantity.map(Self.decimal), "activity.quantity"),
                        try fields.seal(r.price.map(Self.decimal), "activity.price"),
                        try fields.seal(r.fee.map(Self.decimal), "activity.fee"),
                        try fields.seal(r.tax.map(Self.decimal), "activity.tax"),
                        try fields.seal(Self.decimal(r.amount), "activity.amount"), .text(r.currency), try fields.seal(r.raw, "activity.raw_json"),
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
