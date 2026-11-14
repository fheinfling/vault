import CryptoKit
import Foundation
import VaultCore

/// AES-256-GCM fields and domain-separated HMAC-SHA256 equality indexes.
public struct AppleFieldCipher: FieldCipher {
    private let encryptionKey: SymmetricKey
    private let indexKey: SymmetricKey
    public static let keyName = "vault.field-key.v1"

    public enum Failure: Error { case invalidKey, invalidEnvelope }

    public init(secrets: any SecretStore) throws {
        let data = try secrets.getOrCreate(Self.keyName) {
            SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }
        }
        guard data.count == 32 else { throw Failure.invalidKey }
        let master = SymmetricKey(data: data)
        encryptionKey = HKDF<SHA256>.deriveKey(
            inputKeyMaterial: master, info: Data("vault.encryption.v1".utf8), outputByteCount: 32
        )
        indexKey = HKDF<SHA256>.deriveKey(
            inputKeyMaterial: master, info: Data("vault.blind-index.v1".utf8), outputByteCount: 32
        )
    }

    public func seal(_ plaintext: Data, context: String) throws -> Data {
        let box = try AES.GCM.seal(plaintext, using: encryptionKey, authenticating: Data(context.utf8))
        guard let combined = box.combined else { throw Failure.invalidEnvelope }
        return combined
    }

    public func open(_ sealed: Data, context: String) throws -> Data {
        try AES.GCM.open(AES.GCM.SealedBox(combined: sealed), using: encryptionKey, authenticating: Data(context.utf8))
    }

    public func blindIndex(_ value: String, context: String) -> String {
        // Length framing prevents ambiguous (context, value) pairs, including embedded NULs.
        let message = Data("\(context.utf8.count):\(context)".utf8) + Data(value.utf8)
        return HMAC<SHA256>.authenticationCode(for: message, using: indexKey)
            .map { String(format: "%02x", $0) }.joined()
    }
}
