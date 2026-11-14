import Foundation

/// Every source implements this. Connectors talk to providers over URLSession and return plain records.
/// They never touch the database and never see secrets other than their own.
public protocol Connector: Sendable {
    var connectionID: String { get }
    var kind: ConnectionKind { get }

    func accounts() async throws -> [AccountRecord]
    func balances() async throws -> [BalanceRecord]
    func holdings() async throws -> [HoldingRecord]
    func transactions(since: Date?) async throws -> [TransactionRecord]
    func activities(since: Date?) async throws -> [ActivityRecord]
}

public extension Connector {
    // Cash-only sources have no holdings or activities; depot-only sources may have no transactions.
    func holdings() async throws -> [HoldingRecord] { [] }
    func activities(since: Date?) async throws -> [ActivityRecord] { [] }
}

public enum ConnectorError: Error, Sendable, CustomStringConvertible {
    /// The user has to do something first: approve in a banking app, drop a CSV, renew a consent.
    case needsUserAction(String)
    /// The provider is unreachable or returned an error.
    case unavailable(String)
    /// The provider's response could not be understood.
    case decoding(String)
    /// A secret is missing from the secret store.
    case missingSecret(String)
    case notImplemented(String)

    public var description: String {
        switch self {
        case .needsUserAction(let m): return "needs user action: \(m)"
        case .unavailable(let m): return "unavailable: \(m)"
        case .decoding(let m): return "decoding: \(m)"
        case .missingSecret(let m): return "missing secret: \(m)"
        case .notImplemented(let m): return "not implemented: \(m)"
        }
    }
}

// MARK: - Platform adapters
//
// The core only knows these protocols. Apple implementations live in VaultApple.
// A Linux port supplies its own.

public protocol SecretStore: Sendable {
    func get(_ key: String) throws -> Data?
    func set(_ key: String, _ value: Data) throws
    func remove(_ key: String) throws
    /// Atomically creates a missing secret without replacing a concurrent creator's value.
    func getOrCreate(_ key: String, create: () throws -> Data) throws -> Data
}

public extension SecretStore {
    func getString(_ key: String) throws -> String? {
        try get(key).flatMap { String(data: $0, encoding: .utf8) }
    }
    func setString(_ key: String, _ value: String) throws {
        try set(key, Data(value.utf8))
    }
}

public protocol Signer: Sendable {
    func hmacSHA256(key: Data, message: Data) -> Data
    func hmacSHA512(key: Data, message: Data) -> Data
    func sha256(_ data: Data) -> Data
    /// RSASSA-PKCS1-v1_5 with SHA-256, as required for Enable Banking's JWTs.
    func rs256Sign(privateKeyPEM: Data, message: Data) throws -> Data
}

public protocol AuthSession: Sendable {
    /// Opens `url` for the user and returns the callback URL the provider redirects to.
    func authorize(url: URL, callbackScheme: String) async throws -> URL
}

public protocol Scheduler: Sendable {
    func register(intervalSeconds: Int) throws
    func unregister() throws
    func isRegistered() -> Bool
}

public protocol Notifier: Sendable {
    func notify(title: String, body: String) async
}

/// A notifier that only prints. Used by tests and by the CLI when no platform notifier is wired in.
public struct PrintNotifier: Notifier {
    public init() {}
    public func notify(title: String, body: String) async {
        print("[\(title)] \(body)")
    }
}
