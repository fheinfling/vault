import Foundation
import Testing

@testable import VaultCore

private func sampleAccounts() -> [AccountRecord] {
    [
        AccountRecord(externalID: "giro", provider: "testbank", kind: .current, name: "Giro", currency: "EUR"),
        AccountRecord(
            externalID: "depot", provider: "testbank", kind: .depot, name: "Depot", currency: "EUR", isPortfolio: true
        ),
        AccountRecord(
            externalID: "verrechnung", provider: "testbank", kind: .current, name: "Verrechnungskonto",
            currency: "EUR", parentExternalID: "depot"
        ),
    ]
}

private func holding(_ name: String, at: Date, quantity: Int = 1) -> HoldingRecord {
    HoldingRecord(
        accountExternalID: "depot", at: at, isin: nil, name: name, quantity: Decimal(quantity),
        price: nil, value: nil, currency: "EUR"
    )
}

@Test func upsertAccountsIsIdempotentAndReportsNewOnes() async throws {
    let ledger = try Ledger(path: ":memory:")
    try await ledger.upsertConnection(id: "test", kind: .exchange)
    let first = try await ledger.upsertAccounts(connectionID: "test", sampleAccounts())
    let second = try await ledger.upsertAccounts(connectionID: "test", sampleAccounts())
    #expect(first.ids == second.ids)
    #expect(first.ids.count == 3)
    #expect(first.newExternalIDs.sorted() == ["depot", "giro", "verrechnung"])
    #expect(second.newExternalIDs.isEmpty)
    let accounts = try await ledger.accounts()
    #expect(accounts.count == 3)
    #expect(accounts.map(\.externalID).sorted() == ["depot", "giro", "verrechnung"])
}

@Test func latestBalanceWinsAndHistoryIsKept() async throws {
    let ledger = try Ledger(path: ":memory:")
    try await ledger.upsertConnection(id: "test", kind: .exchange)
    try await ledger.upsertAccounts(connectionID: "test", sampleAccounts())
    let earlier = Date(timeIntervalSince1970: 1_700_000_000)
    let later = earlier.addingTimeInterval(3600)
    let run1 = try await ledger.startSyncRun(connectionID: "test")
    let run2 = try await ledger.startSyncRun(connectionID: "test")
    try await ledger.recordBalances(connectionID: "test", runID: run1, [
        BalanceRecord(accountExternalID: "giro", at: earlier, amount: Decimal(string: "100.50")!, currency: "EUR")
    ])
    try await ledger.recordBalances(connectionID: "test", runID: run2, [
        BalanceRecord(accountExternalID: "giro", at: later, amount: Decimal(string: "-12.25")!, currency: "EUR")
    ])
    let giro = try await ledger.accounts().first { $0.externalID == "giro" }
    #expect(giro?.balance == Decimal(string: "-12.25"))
    let history = try await ledger.balanceHistory(accountID: giro!.id)
    #expect(history.map(\.amount) == [Decimal(string: "100.50")!, Decimal(string: "-12.25")!])
}

@Test func oneBalancePerAccountPerRun() async throws {
    let ledger = try Ledger(path: ":memory:")
    try await ledger.upsertConnection(id: "test", kind: .exchange)
    try await ledger.upsertAccounts(connectionID: "test", sampleAccounts())
    let run = try await ledger.startSyncRun(connectionID: "test")
    let at = Date(timeIntervalSince1970: 1_700_000_000)
    try await ledger.recordBalances(connectionID: "test", runID: run, [
        BalanceRecord(accountExternalID: "giro", at: at, amount: 1, currency: "EUR"),
        BalanceRecord(accountExternalID: "giro", at: at, amount: 2, currency: "EUR"),
    ])
    let giro = try await ledger.accounts().first { $0.externalID == "giro" }
    let history = try await ledger.balanceHistory(accountID: giro!.id)
    #expect(history.count == 1)
    #expect(history.first?.amount == 2)
}

@Test func emptiedDepotShowsNoHoldings() async throws {
    let ledger = try Ledger(path: ":memory:")
    try await ledger.upsertConnection(id: "test", kind: .exchange)
    let ids = try await ledger.upsertAccounts(connectionID: "test", sampleAccounts()).ids
    let depot = ids["depot"]!
    let at = Date(timeIntervalSince1970: 1_700_000_000)
    let run1 = try await ledger.startSyncRun(connectionID: "test")
    try await ledger.recordHoldings(connectionID: "test", runID: run1, at: at, [holding("ETF A", at: at)])
    #expect(try await ledger.latestHoldings(accountID: depot).map(\.name) == ["ETF A"])
    // Everything sold: the connector reports no positions for the depot.
    let run2 = try await ledger.startSyncRun(connectionID: "test")
    try await ledger.recordHoldings(connectionID: "test", runID: run2, at: at.addingTimeInterval(3600), [])
    #expect(try await ledger.latestHoldings(accountID: depot).isEmpty)
}

@Test func holdingsWithTheSameProviderTimestampAreNotMerged() async throws {
    let ledger = try Ledger(path: ":memory:")
    try await ledger.upsertConnection(id: "test", kind: .exchange)
    let depot = try await ledger.upsertAccounts(connectionID: "test", sampleAccounts()).ids["depot"]!
    // A provider stamps holdings with its valuation time; two runs four hours apart see the same stamp.
    let valuation = Date(timeIntervalSince1970: 1_700_000_000)
    let run1 = try await ledger.startSyncRun(connectionID: "test")
    try await ledger.recordHoldings(connectionID: "test", runID: run1, at: valuation, [holding("ETF A", at: valuation)])
    let run2 = try await ledger.startSyncRun(connectionID: "test")
    try await ledger.recordHoldings(connectionID: "test", runID: run2, at: valuation, [holding("ETF A", at: valuation, quantity: 2)])
    let latest = try await ledger.latestHoldings(accountID: depot)
    #expect(latest.count == 1)
    #expect(latest.first?.quantity == 2)
}

@Test func transactionsUpsertOnExternalID() async throws {
    let ledger = try Ledger(path: ":memory:")
    try await ledger.upsertConnection(id: "test", kind: .exchange)
    try await ledger.upsertAccounts(connectionID: "test", sampleAccounts())
    let at = Date(timeIntervalSince1970: 1_700_000_000)
    let original = TransactionRecord(
        accountExternalID: "giro", externalID: "t1", bookedAt: at, amount: 10, currency: "EUR", purpose: "first"
    )
    var changed = original
    changed.amount = 11  // the provider corrected the amount after booking
    try await ledger.upsertTransactions(connectionID: "test", [original])
    try await ledger.upsertTransactions(connectionID: "test", [changed])
    #expect(try await ledger.transactionCount(connectionID: "test") == 1)
    #expect(try await ledger.latestBookedAt(connectionID: "test") == at)
}

@Test func cursorsRoundTripPerStream() async throws {
    let ledger = try Ledger(path: ":memory:")
    try await ledger.upsertConnection(id: "test", kind: .exchange)
    #expect(try await ledger.cursor(connectionID: "test", stream: .transactions) == nil)
    let at = Date(timeIntervalSince1970: 1_700_000_000)
    try await ledger.setCursor(connectionID: "test", stream: .transactions, value: at)
    #expect(try await ledger.cursor(connectionID: "test", stream: .transactions) == at)
    #expect(try await ledger.cursor(connectionID: "test", stream: .activities) == nil)
}

@Test func unknownAccountsAreSkippedNotStored() async throws {
    let ledger = try Ledger(path: ":memory:")
    try await ledger.upsertConnection(id: "test", kind: .exchange)
    try await ledger.upsertAccounts(connectionID: "test", sampleAccounts())
    let run = try await ledger.startSyncRun(connectionID: "test")
    let stored = try await ledger.recordBalances(connectionID: "test", runID: run, [
        BalanceRecord(accountExternalID: "nope", at: Date(), amount: 1, currency: "EUR")
    ])
    #expect(stored == 0)
}

@Test func syncRunsAreRecorded() async throws {
    let ledger = try Ledger(path: ":memory:")
    try await ledger.upsertConnection(id: "test", kind: .exchange)
    let id = try await ledger.startSyncRun(connectionID: "test")
    try await ledger.finishSyncRun(id: id, ok: false, accounts: 0, rows: 0, error: "boom")
    let runs = try await ledger.recentSyncRuns()
    #expect(runs.count == 1)
    #expect(runs.first?.ok == false)
    #expect(runs.first?.error == "boom")
}

@Test func bareFileNameOpensInTheWorkingDirectory() async throws {
    let name = "vault-test-\(UUID().uuidString).db"
    defer {
        for suffix in ["", "-wal", "-shm"] { try? FileManager.default.removeItem(atPath: name + suffix) }
    }
    let ledger = try Ledger(path: name)
    try await ledger.upsertConnection(id: "test", kind: .exchange)
    #expect(FileManager.default.fileExists(atPath: name))
}

@Test func v1DatabaseMigratesAndKeepsLegacyHoldings() async throws {
    let path = NSTemporaryDirectory() + "vault-v1-\(UUID().uuidString).db"
    defer {
        for suffix in ["", "-wal", "-shm"] { try? FileManager.default.removeItem(atPath: path + suffix) }
    }
    // Build a v1 database by hand, with holdings that have no run id.
    do {
        let db = try Database(path: path)
        try db.execute(Schema.migrations[0])
        try db.execute("PRAGMA user_version = 1")
        try db.run("INSERT INTO connection (id, kind) VALUES ('old', 'exchange')")
        try db.run(
            "INSERT INTO account (connection_id, external_id, provider, kind, name, currency, is_portfolio) VALUES ('old', 'd', 'p', 'depot', 'D', 'EUR', 1)"
        )
        try db.run(
            "INSERT INTO holding_snapshot (account_id, at, name, quantity, currency) VALUES (1, '2026-01-01T00:00:00Z', 'Legacy', '3', 'EUR')"
        )
    }
    let ledger = try Ledger(path: path)
    let legacy = try await ledger.latestHoldings(accountID: 1)
    #expect(legacy.map(\.name) == ["Legacy"])
    // v2 features work on the migrated database.
    let run = try await ledger.startSyncRun(connectionID: "old")
    try await ledger.recordHoldings(connectionID: "old", runID: run, at: Date(), [])
    #expect(try await ledger.latestHoldings(accountID: 1).isEmpty)
}
