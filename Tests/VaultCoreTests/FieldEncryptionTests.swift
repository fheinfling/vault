import Foundation
import Testing

@testable import VaultCore

private struct Fixture {
    let path = NSTemporaryDirectory() + "vault-fields-\(UUID().uuidString).db"
    static let at = Date(timeIntervalSince1970: 1_700_000_000)
    static let account = AccountRecord(externalID: "DE00TEST", provider: "PrivateBank", kind: .depot,
                                       name: "Giro", ibanMasked: "DE00MASK", currency: "EUR", isPortfolio: true)
    static let holding = HoldingRecord(accountExternalID: "DE00TEST", at: at, isin: "SECRETISIN", name: "PrivateFund",
                                       quantity: Decimal(string: "12.345")!, price: 123, value: Decimal(string: "1518.435")!, currency: "EUR")
    static let transaction = TransactionRecord(accountExternalID: "DE00TEST", externalID: "DE00TX", bookedAt: at,
                                               amount: Decimal(string: "-99.75")!, currency: "EUR", counterparty: "PrivatePerson",
                                               purpose: "PrivatePurpose", category: "PrivateCategory", raw: "{\"privateTX\":true}")
    static let activity = ActivityRecord(accountExternalID: "DE00TEST", externalID: "DE00ACT", at: at, type: .buy,
                                         isin: "SECRETISIN", quantity: 321, price: 456, fee: Decimal(string: "7.89")!,
                                         tax: Decimal(string: "8.91")!, amount: 789, currency: "EUR", raw: "{\"privateACT\":true}")
    static let values: [String: [String: String]] = [
        "account": ["external_id_sealed": "DE00TEST", "provider": "PrivateBank", "name": "Giro", "iban_masked": "DE00MASK"],
        "balance_snapshot": ["amount": "100.5"],
        "holding_snapshot": ["name": "PrivateFund", "isin": "SECRETISIN", "quantity": "12.345", "price": "123", "value": "1518.435"],
        "transaction": ["external_id_sealed": "DE00TX", "amount": "-99.75", "counterparty": "PrivatePerson",
                        "purpose": "PrivatePurpose", "category": "PrivateCategory", "raw_json": "{\"privateTX\":true}"],
        "activity": ["external_id_sealed": "DE00ACT", "isin": "SECRETISIN", "quantity": "321", "price": "456", "fee": "7.89",
                     "tax": "8.91", "amount": "789", "raw_json": "{\"privateACT\":true}"],
    ]

    func remove() {
        for suffix in ["", "-wal", "-shm"] { try? FileManager.default.removeItem(atPath: path + suffix) }
    }

    func write() async throws {
        let ledger = try Ledger(path: path, cipher: TestFieldCipher())
        try await ledger.upsertConnection(id: "test", kind: .exchange)
        try await ledger.upsertAccounts(connectionID: "test", [Self.account])
        let run = try await ledger.startSyncRun(connectionID: "test", at: Self.at)
        try await ledger.recordBalances(connectionID: "test", runID: run, [
            BalanceRecord(accountExternalID: "DE00TEST", at: Self.at, amount: Decimal(string: "100.50")!, currency: "EUR")
        ])
        try await ledger.recordHoldings(connectionID: "test", runID: run, at: Self.at, [Self.holding])
        try await ledger.upsertTransactions(connectionID: "test", [Self.transaction])
        try await ledger.upsertActivities(connectionID: "test", [Self.activity])
    }

    func writeV2() throws {
        let db = try Database(path: path)
        for migration in Schema.migrations.prefix(2) { try db.execute(migration) }
        try db.execute("PRAGMA user_version = 2")
        try db.run("INSERT INTO connection (id, kind) VALUES ('test', 'exchange')")
        for table in ["account", "balance_snapshot", "holding_snapshot", "transaction", "activity"] {
            var values = Self.values[table]!.mapValues(SQLValue.text)
            if let externalID = values.removeValue(forKey: "external_id_sealed") { values["external_id"] = externalID }
            values["currency"] = .text("EUR")
            if table == "account" {
                values["connection_id"] = .text("test")
                values["kind"] = .text("depot")
                values["is_portfolio"] = .integer(1)
            } else {
                values["account_id"] = .integer(1)
                values[table == "transaction" ? "booked_at" : "at"] = .text(Ledger.iso(Self.at))
            }
            if table == "activity" { values["type"] = .text("buy") }
            let columns = values.keys.sorted()
            let placeholders = Array(repeating: "?", count: columns.count).joined(separator: ",")
            try db.run("INSERT INTO \"\(table)\" (\(columns.joined(separator: ","))) VALUES (\(placeholders))", columns.map { values[$0]! })
        }
    }

    func checkStoredValues() throws {
        let db = try Database(path: path)
        #expect(try db.query("PRAGMA user_version").first?["user_version"]?.int64 == Int64(Schema.migrations.count))
        #expect(try db.query("SELECT * FROM field_encryption_cleanup").isEmpty)
        for (table, values) in Self.values {
            let row = try #require(db.query("SELECT * FROM \"\(table)\"").first)
            for (column, expected) in values {
                let envelope = try #require(row[column]?.string)
                #expect(envelope.hasPrefix("v1:"))
                let sealed = try #require(Data(base64Encoded: String(envelope.dropFirst(3))))
                let opened = try TestFieldCipher().open(sealed, context: "\(table).\(column)")
                #expect(String(data: opened, encoding: .utf8) == expected)
            }
            if let original = values["external_id_sealed"] {
                #expect(row["external_id"]?.string == TestFieldCipher().blindIndex(original, context: "\(table).external_id"))
            }
        }
    }

    func checkFiles() throws {
        for suffix in ["", "-wal"] where FileManager.default.fileExists(atPath: path + suffix) {
            let bytes = try Data(contentsOf: URL(fileURLWithPath: path + suffix))
            // Short integer strings could occur in schema text or base64 by chance.
            for value in Self.values.values.flatMap({ $0.values }).filter({ $0.count >= 4 }) {
                #expect(bytes.range(of: Data(value.utf8)) == nil, "plaintext found in database\(suffix): \(value)")
            }
        }
    }

    func checkReadsAndUpserts() async throws {
        let ledger = try Ledger(path: path, cipher: TestFieldCipher())
        let summary = try #require(await ledger.accounts().first)
        #expect(summary.name == "Giro")
        #expect(summary.provider == "PrivateBank")
        #expect(summary.externalID == "DE00TEST")
        #expect(summary.balance == Decimal(string: "100.50"))
        #expect(try await ledger.balanceHistory(accountID: summary.id).first?.amount == summary.balance)
        #expect(try await ledger.latestHoldings(accountID: summary.id) == [Self.holding])
        let upsert = try await ledger.upsertAccounts(connectionID: "test", [Self.account])
        #expect(upsert.newExternalIDs.isEmpty)
        #expect(upsert.ids == ["DE00TEST": summary.id])
        var transaction = Self.transaction
        transaction.amount = 555
        var activity = Self.activity
        activity.amount = 666
        try await ledger.upsertTransactions(connectionID: "test", [transaction])
        try await ledger.upsertActivities(connectionID: "test", [activity])
        #expect(try await ledger.transactionCount(connectionID: "test") == 1)
        #expect(try await ledger.activityCount(connectionID: "test") == 1)
        let db = try Database(path: path)
        for (table, amount) in [("transaction", "555"), ("activity", "666")] {
            let row = try #require(db.query("SELECT amount FROM \"\(table)\"").first)
            #expect(try FieldStorage(cipher: TestFieldCipher()).open(row["amount"], "\(table).amount") == amount)
        }
    }
}

@Test func sensitiveValuesAreAbsentFromDatabaseFiles() async throws {
    let fixture = Fixture()
    defer { fixture.remove() }
    try await fixture.write()
    try fixture.checkStoredValues()
    try fixture.checkFiles()
    try await fixture.checkReadsAndUpserts()
}

@Test func v2PlaintextMigratesOnceAndKeepsUpsertIdentity() async throws {
    let fixture = Fixture()
    defer { fixture.remove() }
    try fixture.writeV2()
    // Converting must migrate every table and remove old plaintext pages and WAL frames.
    let ledger = try Ledger(path: fixture.path, cipher: TestFieldCipher(), legacy: .convert(backupPath: nil))
    #expect(ledger.convertedLegacyVersion == 2)
    try fixture.checkStoredValues()
    try fixture.checkFiles()
    try await fixture.checkReadsAndUpserts()
}

@Test func copiedCiphertextAndPlaintextReplacementFailClosed() async throws {
    let fixture = Fixture()
    defer { fixture.remove() }
    try await fixture.write()
    let ledger = try Ledger(path: fixture.path, cipher: TestFieldCipher())
    let db = try Database(path: fixture.path)
    try db.run("UPDATE account SET name = provider")
    await #expect(throws: TestFieldCipher.Failure.self) { try await ledger.accounts() }
    try db.run("UPDATE account SET name = 'plaintext replacement'")
    await #expect(throws: FieldEncodingError.self) { try await ledger.accounts() }
    try db.run("UPDATE account SET name = 'v1:invalid!'")
    await #expect(throws: FieldEncodingError.self) { try await ledger.accounts() }
}

private struct FailingMigrationCipher: FieldCipher {
    func seal(_ plaintext: Data, context: String) throws -> Data {
        if context == "account.external_id_sealed" { throw TestFieldCipher.Failure.context }
        return try TestFieldCipher().seal(plaintext, context: context)
    }
    func open(_ sealed: Data, context: String) throws -> Data { try TestFieldCipher().open(sealed, context: context) }
    func blindIndex(_ value: String, context: String) -> String { TestFieldCipher().blindIndex(value, context: context) }
}

@Test func failedMigrationRollsBackSchemaAndDataAndCanRetry() throws {
    let fixture = Fixture()
    defer { fixture.remove() }
    try fixture.writeV2()
    #expect(throws: TestFieldCipher.Failure.self) {
        try Ledger(path: fixture.path, cipher: FailingMigrationCipher(), legacy: .convert(backupPath: nil))
    }
    do {
        let db = try Database(path: fixture.path)
        #expect(try db.query("PRAGMA user_version").first?["user_version"]?.int64 == 2)
        #expect(try db.query("SELECT name, external_id FROM account").first?["name"]?.string == "Giro")
        #expect(try db.query("PRAGMA table_info(account)").allSatisfy { $0["name"]?.string != "external_id_sealed" })
    }
    _ = try Ledger(path: fixture.path, cipher: TestFieldCipher(), legacy: .convert(backupPath: nil))
    try fixture.checkStoredValues()
    try fixture.checkFiles()
}

@Test func pendingPlaintextCleanupIsRetriedOnOpen() async throws {
    let fixture = Fixture()
    defer { fixture.remove() }
    try await fixture.write()
    do {
        let db = try Database(path: fixture.path)
        try db.run("INSERT INTO field_encryption_cleanup VALUES (1)")
    }
    _ = try Ledger(path: fixture.path, cipher: TestFieldCipher())
    try fixture.checkStoredValues()
}

@Test func encryptedNamesSortAfterDecryptionAndParentLookupUsesIndex() async throws {
    let fixture = Fixture()
    defer { fixture.remove() }
    let ledger = try Ledger(path: fixture.path, cipher: TestFieldCipher())
    try await ledger.upsertConnection(id: "test", kind: .exchange)
    let records = [
        AccountRecord(externalID: "z", provider: "B", kind: .current, name: "A", currency: "EUR"),
        AccountRecord(externalID: "p", provider: "A", kind: .depot, name: "A", currency: "EUR", isPortfolio: true),
        AccountRecord(externalID: "b", provider: "A", kind: .current, name: "B", currency: "EUR", parentExternalID: "p"),
        AccountRecord(externalID: "a", provider: "A", kind: .current, name: "A", currency: "EUR"),
    ]
    let ids = try await ledger.upsertAccounts(connectionID: "test", records).ids
    #expect(try await ledger.accounts().map(\.externalID) == ["a", "b", "p", "z"])
    let db = try Database(path: fixture.path)
    #expect(try db.query("SELECT parent_id FROM account WHERE id = ?", [.integer(ids["b"]!)]).first?["parent_id"]?.int64 == ids["p"])
    let run = try await ledger.startSyncRun(connectionID: "test")
    let holdings = ["Z", "A"].map {
        HoldingRecord(accountExternalID: "p", at: Fixture.at, isin: nil, name: $0, quantity: 1, price: nil, value: nil, currency: "EUR")
    }
    try await ledger.recordHoldings(connectionID: "test", runID: run, at: Fixture.at, holdings)
    #expect(try await ledger.latestHoldings(accountID: ids["p"]!).map(\.name) == ["A", "Z"])
}

@Test func legacyPlaintextWithEnvelopePrefixIsSealedDuringMigration() throws {
    let fixture = Fixture()
    defer { fixture.remove() }
    try fixture.writeV2()
    let purpose = "v1: invoice correction"
    do {
        let db = try Database(path: fixture.path)
        try db.run("UPDATE \"transaction\" SET purpose = ?", [.text(purpose)])
    }
    _ = try Ledger(path: fixture.path, cipher: TestFieldCipher(), legacy: .convert(backupPath: nil))
    let db = try Database(path: fixture.path)
    let row = try #require(db.query("SELECT purpose FROM \"transaction\"").first)
    #expect(try FieldStorage(cipher: TestFieldCipher()).open(row["purpose"], "transaction.purpose") == purpose)
}

/// Issues the statements a pre-v3 binary (base commit 982b1b8) issues, with plaintext external IDs and values,
/// and expects each to abort without adding a row.
private func expectPreV3WritesAbort(_ db: Database) throws {
    let legacyWrites: [(String, [SQLValue])] = [
        ("""
         INSERT INTO account (connection_id, external_id, provider, kind, name, iban_masked, currency, is_portfolio, hidden)
         VALUES ('test', 'DE00TEST', 'PrivateBank', 'depot', 'Giro', NULL, 'EUR', 1, 0)
         ON CONFLICT(connection_id, external_id) DO UPDATE SET name = excluded.name
         """, []),
        ("INSERT INTO balance_snapshot (account_id, run_id, at, amount, currency) VALUES (1, NULL, ?, '1.00', 'EUR')",
         [.text(Ledger.iso(Fixture.at))]),
        ("""
         INSERT INTO holding_snapshot (account_id, run_id, at, isin, name, quantity, price, value, currency)
         VALUES (1, NULL, ?, NULL, 'Fund', '1', NULL, NULL, 'EUR')
         """, [.text(Ledger.iso(Fixture.at))]),
        ("""
         INSERT INTO "transaction" (account_id, external_id, booked_at, amount, currency) VALUES (1, 'DE00TX', ?, '1.00', 'EUR')
         ON CONFLICT(account_id, external_id) DO UPDATE SET amount = excluded.amount
         """, [.text(Ledger.iso(Fixture.at))]),
        ("""
         INSERT INTO activity (account_id, external_id, at, type, amount, currency) VALUES (1, 'DE00ACT', ?, 'buy', '1.00', 'EUR')
         ON CONFLICT(account_id, external_id) DO UPDATE SET amount = excluded.amount
         """, [.text(Ledger.iso(Fixture.at))]),
    ]
    for (sql, values) in legacyWrites {
        #expect(throws: DatabaseError.self, "pre-v3 write must abort: \(sql)") { try db.run(sql, values) }
    }
    for table in ["account", "balance_snapshot", "holding_snapshot", "transaction", "activity"] {
        #expect(try db.query("SELECT COUNT(*) AS n FROM \"\(table)\"").first?["n"]?.int64 == 1, "\(table) gained a row")
    }
}

@Test func preV3WritersAbortOnMigratedDatabase() async throws {
    let fixture = Fixture()
    defer { fixture.remove() }
    try await fixture.write()
    _ = try Ledger(path: fixture.path, cipher: TestFieldCipher())
    try expectPreV3WritesAbort(try Database(path: fixture.path))
    try fixture.checkStoredValues()
}

/// A database sealed by the first encryption revision (2810028) is at v3 and has no writer triggers.
@Test func v3DatabaseGainsWriterTriggersWithoutRewritingValues() throws {
    let fixture = Fixture()
    defer { fixture.remove() }
    try fixture.writeV2()
    let sealedColumns = "SELECT * FROM account, balance_snapshot, holding_snapshot, \"transaction\", activity"
    let before: [[String: SQLValue]]
    do {
        let db = try Database(path: fixture.path)
        try db.transaction {
            try db.execute(Schema.migrations[2])
            try FieldStorage(cipher: TestFieldCipher()).migrate(db)
            try db.execute("PRAGMA user_version = 3")
        }
        #expect(try db.query("SELECT name FROM sqlite_master WHERE type = 'trigger'").isEmpty)
        before = try db.query(sealedColumns)
    }
    let ledger = try Ledger(path: fixture.path, cipher: TestFieldCipher())
    #expect(ledger.convertedLegacyVersion == nil)
    let db = try Database(path: fixture.path)
    let triggers = try db.query("SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name").compactMap { $0["name"]?.string }
    #expect(triggers == ["account", "activity", "balance_snapshot", "holding_snapshot", "transaction"].map { "\($0)_requires_v3" })
    #expect(try db.query(sealedColumns) == before)
    try expectPreV3WritesAbort(db)
    try fixture.checkStoredValues()
}

@Test func legacyDatabaseIsLeftUntouchedWithoutExplicitConversion() throws {
    let fixture = Fixture()
    defer { fixture.remove() }
    try fixture.writeV2()
    let before = try Data(contentsOf: URL(fileURLWithPath: fixture.path))
    #expect(throws: LegacyDatabaseError.self) { try Ledger(path: fixture.path, cipher: TestFieldCipher()) }
    let db = try Database(path: fixture.path)
    #expect(try db.query("PRAGMA user_version").first?["user_version"]?.int64 == 2)
    #expect(try db.query("SELECT name FROM account").first?["name"]?.string == "Giro")
    #expect(try db.query("PRAGMA table_info(account)").allSatisfy { $0["name"]?.string != "external_id_sealed" })
    #expect(try db.query("SELECT name FROM sqlite_master WHERE name = 'field_encryption_cleanup'").isEmpty)
    #expect(try Data(contentsOf: URL(fileURLWithPath: fixture.path)) == before)
}

@Test func conversionWithBackupWritesVerifiedPlaintextCopyFirst() throws {
    let fixture = Fixture()
    let backup = fixture.path + ".pre-v3"
    defer {
        fixture.remove()
        for suffix in ["", "-wal", "-shm"] { try? FileManager.default.removeItem(atPath: backup + suffix) }
    }
    try fixture.writeV2()
    let ledger = try Ledger(path: fixture.path, cipher: TestFieldCipher(), legacy: .convert(backupPath: backup))
    #expect(ledger.convertedLegacyVersion == 2)
    try fixture.checkStoredValues()
    try fixture.checkFiles()
    let mode = try FileManager.default.attributesOfItem(atPath: backup)[.posixPermissions] as? Int
    #expect(mode == 0o600)
    // The copy is exactly what a pre-v3 binary expects: schema v2 with plaintext rows.
    let copy = try Database(path: backup)
    #expect(try copy.query("PRAGMA user_version").first?["user_version"]?.int64 == 2)
    #expect(try copy.query("SELECT name, external_id FROM account").first == ["name": .text("Giro"), "external_id": .text("DE00TEST")])
    #expect(try copy.query("SELECT name FROM sqlite_master WHERE type = 'trigger'").isEmpty)
}

@Test func existingBackupFileStopsConversion() throws {
    let fixture = Fixture()
    let backup = fixture.path + ".pre-v3"
    defer {
        fixture.remove()
        try? FileManager.default.removeItem(atPath: backup)
    }
    try fixture.writeV2()
    try Data("keep me".utf8).write(to: URL(fileURLWithPath: backup))
    #expect(throws: DatabaseError.self) {
        try Ledger(path: fixture.path, cipher: TestFieldCipher(), legacy: .convert(backupPath: backup))
    }
    #expect(try Data(contentsOf: URL(fileURLWithPath: backup)) == Data("keep me".utf8))
    let db = try Database(path: fixture.path)
    #expect(try db.query("PRAGMA user_version").first?["user_version"]?.int64 == 2)
    #expect(try db.query("SELECT name FROM account").first?["name"]?.string == "Giro")
}
