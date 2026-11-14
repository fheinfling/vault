import Foundation
import VaultCore

/// Deliberately insecure, deterministic fixture cipher. Never use outside tests.
struct TestFieldCipher: FieldCipher {
    enum Failure: Error { case context }

    func seal(_ plaintext: Data, context: String) throws -> Data {
        Data((Data(context.utf8) + Data([0]) + plaintext).map { $0 ^ 0xa5 })
    }

    func open(_ sealed: Data, context: String) throws -> Data {
        let decoded = Data(sealed.map { $0 ^ 0xa5 })
        let prefix = Data(context.utf8) + Data([0])
        guard decoded.starts(with: prefix) else { throw Failure.context }
        return Data(decoded.dropFirst(prefix.count))
    }

    func blindIndex(_ value: String, context: String) -> String {
        // A reversible stand-in for a keyed MAC; production uses HMAC-SHA256.
        (Data(context.utf8) + Data([0]) + Data(value.utf8)).map { String(format: "%02x", $0 ^ 0x5a) }.joined()
    }
}
