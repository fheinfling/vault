import FinTSKit
import Foundation
import VaultCore

/// DKB over FinTS. Needs our own registered FinTS product ID. Read-only by construction: FinTSKit has no payment segments.
public struct DKBConnector: Connector {
    public let connectionID = "dkb"
    public let kind = ConnectionKind.dkb

    public init() {}

    public func accounts() async throws -> [AccountRecord] {
        throw ConnectorError.notImplemented("DKB over FinTS")
    }
    public func balances() async throws -> [BalanceRecord] { [] }
    public func transactions(since: Date?) async throws -> [TransactionRecord] { [] }
}
