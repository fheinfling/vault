import Foundation
import Testing

@testable import VaultCore

/// Records what the engine asked for and lets a test script failures. Locked, so it is Sendable.
private final class Script: @unchecked Sendable {
    private let lock = NSLock()
    private var _accounts: [AccountRecord]
    private var _activityFailures = 0
    private(set) var transactionsSince: [Date?] = []
    private(set) var activitiesSince: [Date?] = []

    init(accounts: [AccountRecord], activityFailures: Int = 0) {
        _accounts = accounts
        _activityFailures = activityFailures
    }

    var accounts: [AccountRecord] {
        get { lock.withLock { _accounts } }
        set { lock.withLock { _accounts = newValue } }
    }
    func failNextActivities(_ count: Int) { lock.withLock { _activityFailures = count } }
    func sawTransactions(_ since: Date?) { lock.withLock { transactionsSince.append(since) } }
    func sawActivities(_ since: Date?) -> Bool {
        lock.withLock {
            activitiesSince.append(since)
            if _activityFailures > 0 {
                _activityFailures -= 1
                return false
            }
            return true
        }
    }
}

private struct FakeConnector: Connector {
    let connectionID = "fake"
    let kind = ConnectionKind.exchange
    var failWith: ConnectorError? = nil
    var script: Script

    static let day: TimeInterval = 24 * 3600
    static let t0 = Date(timeIntervalSince1970: 1_700_000_000)

    func accounts() async throws -> [AccountRecord] {
        if let failWith { throw failWith }
        return script.accounts
    }
    func balances() async throws -> [BalanceRecord] {
        script.accounts.map { BalanceRecord(accountExternalID: $0.externalID, at: Self.t0, amount: 42, currency: "EUR") }
    }
    func transactions(since: Date?) async throws -> [TransactionRecord] {
        script.sawTransactions(since)
        return [TransactionRecord(accountExternalID: "a", externalID: "x", bookedAt: Self.t0, amount: 1, currency: "EUR")]
    }
    func activities(since: Date?) async throws -> [ActivityRecord] {
        guard script.sawActivities(since) else { throw ConnectorError.unavailable("activities down") }
        return [ActivityRecord(accountExternalID: "a", externalID: "y", at: Self.t0.addingTimeInterval(Self.day), type: .buy, amount: 5, currency: "EUR")]
    }
}

private final class RecordingNotifier: Notifier, @unchecked Sendable {
    var messages: [String] = []
    func notify(title: String, body: String) async { messages.append("\(title): \(body)") }
}

private let accountA = AccountRecord(externalID: "a", provider: "fake", kind: .crypto, name: "Wallet", currency: "EUR")
private let accountB = AccountRecord(externalID: "b", provider: "fake", kind: .crypto, name: "Second", currency: "EUR")

@Test func successfulSyncStoresEverythingAndSetsCursors() async throws {
    let ledger = try Ledger(path: ":memory:", cipher: TestFieldCipher())
    let engine = SyncEngine(ledger: ledger)
    let result = await engine.sync(FakeConnector(script: Script(accounts: [accountA])))
    #expect(result.ok)
    #expect(result.accounts == 1)
    #expect(result.rows == 3)  // one balance, one transaction, one activity
    let accounts = try await ledger.accounts()
    #expect(accounts.first?.balance == 42)
    #expect(try await ledger.recentSyncRuns().first?.ok == true)
    #expect(try await ledger.cursor(connectionID: "fake", stream: .transactions) == FakeConnector.t0)
    #expect(try await ledger.cursor(connectionID: "fake", stream: .activities) == FakeConnector.t0.addingTimeInterval(FakeConnector.day))
}

@Test func secondSyncIsIncrementalWithOverlap() async throws {
    let ledger = try Ledger(path: ":memory:", cipher: TestFieldCipher())
    let engine = SyncEngine(ledger: ledger)
    let script = Script(accounts: [accountA])
    _ = await engine.sync(FakeConnector(script: script))
    _ = await engine.sync(FakeConnector(script: script))
    #expect(script.transactionsSince == [nil, FakeConnector.t0.addingTimeInterval(-SyncEngine.overlap)])
    #expect(script.activitiesSince == [nil, FakeConnector.t0.addingTimeInterval(FakeConnector.day - SyncEngine.overlap)])
}

@Test func failedRunDoesNotAdvanceAnyCursor() async throws {
    let ledger = try Ledger(path: ":memory:", cipher: TestFieldCipher())
    let engine = SyncEngine(ledger: ledger)
    let script = Script(accounts: [accountA], activityFailures: 1)
    let first = await engine.sync(FakeConnector(script: script))
    #expect(!first.ok)
    #expect(try await ledger.transactionCount(connectionID: "fake") == 1)  // the transactions were stored
    #expect(try await ledger.cursor(connectionID: "fake", stream: .transactions) == nil)  // but nothing moved
    let second = await engine.sync(FakeConnector(script: script))
    #expect(second.ok)
    // The retry asks for full history on both streams, so the activities that failed are not lost.
    #expect(script.transactionsSince == [nil, nil])
    #expect(script.activitiesSince == [nil, nil])
    #expect(try await ledger.activityCount(connectionID: "fake") == 1)
}

@Test func newAccountTriggersFullHistory() async throws {
    let ledger = try Ledger(path: ":memory:", cipher: TestFieldCipher())
    let engine = SyncEngine(ledger: ledger)
    let script = Script(accounts: [accountA])
    _ = await engine.sync(FakeConnector(script: script))
    _ = await engine.sync(FakeConnector(script: script))
    script.accounts = [accountA, accountB]  // the user opened another account at this provider
    _ = await engine.sync(FakeConnector(script: script))
    _ = await engine.sync(FakeConnector(script: script))
    #expect(script.transactionsSince.map { $0 == nil } == [true, false, true, false])
}

@Test func newAccountStillGetsFullHistoryWhenItsFirstRunFails() async throws {
    let ledger = try Ledger(path: ":memory:", cipher: TestFieldCipher())
    let engine = SyncEngine(ledger: ledger)
    let script = Script(accounts: [accountA])
    #expect(await engine.sync(FakeConnector(script: script)).ok)
    script.accounts = [accountA, accountB]
    script.failNextActivities(1)  // the run that first sees account b fails half-way
    #expect(await !engine.sync(FakeConnector(script: script)).ok)
    #expect(await engine.sync(FakeConnector(script: script)).ok)
    // The retry still owes b its full history on both streams.
    #expect(script.transactionsSince.map { $0 == nil } == [true, true, true])
    #expect(script.activitiesSince.map { $0 == nil } == [true, true, true])
    _ = await engine.sync(FakeConnector(script: script))
    #expect(script.transactionsSince.last! != nil)  // and once a run succeeds, sync is incremental again
}

@Test func needsUserActionIsFlaggedAndNotified() async throws {
    let ledger = try Ledger(path: ":memory:", cipher: TestFieldCipher())
    let notifier = RecordingNotifier()
    let engine = SyncEngine(ledger: ledger, notifier: notifier)
    let result = await engine.sync(
        FakeConnector(failWith: .needsUserAction("approve in the app"), script: Script(accounts: [accountA]))
    )
    #expect(!result.ok)
    #expect(notifier.messages.count == 1)
    #expect(try await ledger.recentSyncRuns().first?.error?.contains("approve in the app") == true)
}
