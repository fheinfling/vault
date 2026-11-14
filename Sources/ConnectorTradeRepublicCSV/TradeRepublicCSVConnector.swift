import Foundation
import VaultCore

/// Trade Republic's own CSV export (app: Profile → Kontoauszüge → Transaktionsexport), read from a watched folder.
/// Not implemented yet.
public struct TradeRepublicCSVConnector: Connector {
    public let connectionID = "tr_csv"
    public let kind = ConnectionKind.tradeRepublicCSV
    public let folder: URL

    public init(folder: URL) {
        self.folder = folder
    }

    public func accounts() async throws -> [AccountRecord] {
        throw ConnectorError.notImplemented("Trade Republic CSV import")
    }
    public func balances() async throws -> [BalanceRecord] { [] }
    public func transactions(since: Date?) async throws -> [TransactionRecord] { [] }
}
