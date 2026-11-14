import Foundation
import VaultCore

/// Scalable Capital's official MCP ("Agentic Investing"), read-only, through a small JSON-RPC-over-HTTP client.
/// Spike first: can our own client authenticate, and does it return transaction history or only holdings?
public struct ScalableConnector: Connector {
    public let connectionID = "scalable"
    public let kind = ConnectionKind.scalable

    private let secrets: any SecretStore

    public init(secrets: any SecretStore) {
        self.secrets = secrets
    }

    public func accounts() async throws -> [AccountRecord] {
        throw ConnectorError.notImplemented("Scalable MCP")
    }
    public func balances() async throws -> [BalanceRecord] { [] }
    public func transactions(since: Date?) async throws -> [TransactionRecord] { [] }
}
