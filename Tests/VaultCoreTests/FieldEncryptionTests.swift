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

    func checkStoredValues() throws {
        let db = try Database(path: path)
        #expect(try db.query("PRAGMA user_version").first?["user_version"]?.int64 == 4)
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
