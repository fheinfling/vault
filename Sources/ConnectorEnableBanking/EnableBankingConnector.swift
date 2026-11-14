import Foundation
import VaultCore

/// Enable Banking, free restricted mode, for the Trade Republic cash account over its official PSD2 interface.
/// Authenticates with an RS256 JWT (via `Signer`) and a 180-day consent (via `AuthSession`).
public struct EnableBankingConnector: Connector {
    public let connectionID = "enable_banking"
    public let kind = ConnectionKind.enableBanking

    private let secrets: any SecretStore
    private let signer: any Signer

    public init(secrets: any SecretStore, signer: any Signer) {
        self.secrets = secrets
        self.signer = signer
    }

    public func accounts() async throws -> [AccountRecord] {
        throw ConnectorError.notImplemented("Enable Banking")
    }
    public func balances() async throws -> [BalanceRecord] { [] }
    public func transactions(since: Date?) async throws -> [TransactionRecord] { [] }
}
