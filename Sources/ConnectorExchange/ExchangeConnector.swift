import Foundation
import VaultCore

/// A crypto exchange over its official REST API with a read-only API key. Direct `URLSession` calls, HMAC via `Signer`.
/// Each exchange gets a small, fully reviewed adapter when it is added; its API host goes into
/// `scripts/egress/vault.hosts`. None is configured yet.
public struct ExchangeConnector: Connector {
    /// A short, stable identifier for the exchange, e.g. its domain name without the TLD.
    public let exchangeID: String
    public var connectionID: String { "exchange_\(exchangeID)" }
    public let kind = ConnectionKind.exchange

    private let secrets: any SecretStore
    private let signer: any Signer

    public init(exchangeID: String, secrets: any SecretStore, signer: any Signer) {
        self.exchangeID = exchangeID
        self.secrets = secrets
        self.signer = signer
    }

    public func accounts() async throws -> [AccountRecord] {
        throw ConnectorError.notImplemented("exchange \(exchangeID): no adapter yet")
    }
    public func balances() async throws -> [BalanceRecord] { [] }
    public func transactions(since: Date?) async throws -> [TransactionRecord] { [] }
}
