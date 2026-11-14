import Foundation
import Security
import VaultCore

/// Generic-password items in the login keychain, one per secret, under a single service name.
///
/// Items created by a signed binary carry an access list naming that binary, so another program
/// asking for the item triggers a Keychain prompt. Once the vault ships as a signed app with
/// entitlements, switch to the data-protection keychain (`kSecUseDataProtectionKeychain`),
/// which binds items to the team identifier instead. Until then `kSecAttrAccessible` below has no
/// effect: the file-based keychain ignores it, so items are not device-bound. An unsigned binary
/// asking for the data-protection keychain gets `errSecMissingEntitlement` (-34018).
public struct KeychainSecretStore: SecretStore {
    public let service: String

    public init(service: String = "com.franzheinfling.vault") {
        self.service = service
    }

    public func get(_ key: String) throws -> Data? {
        var query = baseQuery(key)
        query[kSecReturnData] = true
        query[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        switch status {
        case errSecSuccess:
            return result as? Data
        case errSecItemNotFound:
            return nil
        default:
            throw KeychainError(status: status, operation: "read \(key)")
        }
    }

    public func set(_ key: String, _ value: Data) throws {
        let query = baseQuery(key)
        let update: [CFString: Any] = [kSecValueData: value]
        var status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var insert = query
            insert[kSecValueData] = value
            insert[kSecAttrAccessible] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            status = SecItemAdd(insert as CFDictionary, nil)
        }
        guard status == errSecSuccess else {
            throw KeychainError(status: status, operation: "write \(key)")
        }
    }

    public func getOrCreate(_ key: String, create: () throws -> Data) throws -> Data {
        if let existing = try get(key) { return existing }
        let value = try create()
        var insert = baseQuery(key)
        insert[kSecValueData] = value
        insert[kSecAttrAccessible] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(insert as CFDictionary, nil)
        if status == errSecSuccess { return value }
        // Another CLI process may have created the shared field key since our read.
        if status == errSecDuplicateItem, let existing = try get(key) { return existing }
        throw KeychainError(status: status, operation: "create \(key)")
    }

    public func remove(_ key: String) throws {
        let status = SecItemDelete(baseQuery(key) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError(status: status, operation: "delete \(key)")
        }
    }

    private func baseQuery(_ key: String) -> [CFString: Any] {
        [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: key,
        ]
    }
}

public struct KeychainError: Error, CustomStringConvertible {
    public let status: OSStatus
    public let operation: String
    public var description: String {
        let message = SecCopyErrorMessageString(status, nil) as String? ?? "unknown"
        return "keychain \(operation): \(message) (\(status))"
    }
}
