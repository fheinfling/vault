import Foundation

public struct SyncResult: Sendable, Equatable {
    public var connectionID: String
    public var ok: Bool
    public var accounts: Int
    public var rows: Int
    public var message: String?
}

/// Runs connectors against the ledger and records the outcome. Never throws: every outcome is a `SyncResult`.
///
/// Incremental fetches use one cursor per stream (transactions, activities), stored only after a run
/// succeeded end to end. A run that fails half-way therefore repeats everything next time instead of
/// skipping the part that failed. When a connection reports an account the ledger has not seen, the
/// ledger drops the connection's cursors in the same transaction that stores the account, so every run
/// fetches full history until one succeeds. A new account gets its past rather than only the window after
/// it appeared, even when the run that first saw it failed.
public struct SyncEngine: Sendable {
    /// How far back incremental fetches overlap, because some providers change amounts after booking.
    public static let overlap: TimeInterval = 14 * 24 * 3600

    private let ledger: Ledger
    private let notifier: any Notifier

    public init(ledger: Ledger, notifier: any Notifier = PrintNotifier()) {
        self.ledger = ledger
        self.notifier = notifier
    }

    public func sync(_ connectors: [any Connector]) async -> [SyncResult] {
        var results: [SyncResult] = []
        for connector in connectors {
            results.append(await sync(connector))
        }
        return results
    }

    public func sync(_ connector: any Connector) async -> SyncResult {
        let id = connector.connectionID
        var runID: Int64?
        var accountCount = 0
        var rowCount = 0
        do {
            try await ledger.upsertConnection(id: id, kind: connector.kind)
            let run = try await ledger.startSyncRun(connectionID: id)
            runID = run
            let now = Date()

            let accounts = try await connector.accounts()
            try await ledger.upsertAccounts(connectionID: id, accounts)
            accountCount = accounts.count

            let balances = try await connector.balances()
            rowCount += try await ledger.recordBalances(connectionID: id, runID: run, balances)

            let holdings = try await connector.holdings()
            rowCount += try await ledger.recordHoldings(connectionID: id, runID: run, at: now, holdings)

            let transactionsSince = try await since(id, .transactions)
            let transactions = try await connector.transactions(since: transactionsSince)
            rowCount += try await ledger.upsertTransactions(connectionID: id, transactions)

            let activitiesSince = try await since(id, .activities)
            let activities = try await connector.activities(since: activitiesSince)
            rowCount += try await ledger.upsertActivities(connectionID: id, activities)

            // Everything succeeded: now, and only now, move the cursors.
            if let newest = transactions.map(\.bookedAt).max() {
                try await ledger.setCursor(connectionID: id, stream: .transactions, value: newest)
            }
            if let newest = activities.map(\.at).max() {
                try await ledger.setCursor(connectionID: id, stream: .activities, value: newest)
            }
            try await ledger.markSynced(connectionID: id)
            try await ledger.finishSyncRun(id: run, ok: true, accounts: accountCount, rows: rowCount, error: nil)
            return SyncResult(connectionID: id, ok: true, accounts: accountCount, rows: rowCount, message: nil)
        } catch let error as ConnectorError {
            if case .needsUserAction(let message) = error {
                try? await ledger.setNeedsUserAction(connectionID: id, message: message)
                await notifier.notify(title: "vault: \(id) needs you", body: message)
            }
            return await fail(id: id, runID: runID, accounts: accountCount, rows: rowCount, message: error.description)
        } catch {
            return await fail(id: id, runID: runID, accounts: accountCount, rows: rowCount, message: "\(error)")
        }
    }

    private func since(_ connectionID: String, _ stream: SyncStream) async throws -> Date? {
        try await ledger.cursor(connectionID: connectionID, stream: stream).map { $0.addingTimeInterval(-Self.overlap) }
    }

    private func fail(id: String, runID: Int64?, accounts: Int, rows: Int, message: String) async -> SyncResult {
        if let runID {
            try? await ledger.finishSyncRun(id: runID, ok: false, accounts: accounts, rows: rows, error: message)
        }
        return SyncResult(connectionID: id, ok: false, accounts: accounts, rows: rows, message: message)
    }
}
