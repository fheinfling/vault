import Foundation
import Testing
import VaultCore

@testable import VaultApple

private final class MemorySecrets: SecretStore, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: Data] = [:]
    private var creations = 0
    var creationCount: Int { lock.withLock { creations } }
    func get(_ key: String) throws -> Data? { lock.withLock { values[key] } }
    func set(_ key: String, _ value: Data) throws { lock.withLock { values[key] = value } }
    func remove(_ key: String) throws { lock.withLock { _ = values.removeValue(forKey: key) } }
    func getOrCreate(_ key: String, create: () throws -> Data) throws -> Data {
        try lock.withLock {
            if let value = values[key] { return value }
            let value = try create()
            values[key] = value
            creations += 1
            return value
        }
    }
}

@Test func appleFieldsRoundTripAndUseFreshNonces() throws {
    let cipher = try AppleFieldCipher(secrets: MemorySecrets())
    for plaintext in [Data(), Data("Giro – 100.50 €\u{0}".utf8), Data([0, 255, 1])] {
        let sealed = try cipher.seal(plaintext, context: "account.name")
        #expect(try cipher.open(sealed, context: "account.name") == plaintext)
        #expect(try cipher.seal(plaintext, context: "account.name") != sealed)
    }
}

@Test func appleFieldsRejectTamperingWrongContextAndWrongKey() throws {
    let cipher = try AppleFieldCipher(secrets: MemorySecrets())
    let sealed = try cipher.seal(Data("Giro".utf8), context: "account.name")
    var corrupted = sealed
    corrupted[corrupted.count - 1] ^= 1
    #expect(throws: (any Error).self) { try cipher.open(corrupted, context: "account.name") }
    #expect(throws: (any Error).self) { try cipher.open(sealed, context: "account.provider") }
    #expect(throws: (any Error).self) { try cipher.open(Data(sealed.prefix(10)), context: "account.name") }
    let other = try AppleFieldCipher(secrets: MemorySecrets())
    #expect(throws: (any Error).self) { try other.open(sealed, context: "account.name") }
}

@Test func appleFieldKeyIsCreatedOnceAndReused() async throws {
    let secrets = MemorySecrets()
    let first = try AppleFieldCipher(secrets: secrets)
    let sealed = try first.seal(Data("Giro".utf8), context: "account.name")
    try await withThrowingTaskGroup(of: Void.self) { group in
        for _ in 0..<20 {
            group.addTask {
                let next = try AppleFieldCipher(secrets: secrets)
                #expect(try next.open(sealed, context: "account.name") == Data("Giro".utf8))
                #expect(next.blindIndex("DE00TEST", context: "account.external_id") == first.blindIndex("DE00TEST", context: "account.external_id"))
            }
        }
        try await group.waitForAll()
    }
    #expect(secrets.creationCount == 1)
    #expect(try secrets.get(AppleFieldCipher.keyName)?.count == 32)
}

@Test func appleBlindIndexesAreHexStableAndDomainSeparated() throws {
    let cipher = try AppleFieldCipher(secrets: MemorySecrets())
    let index = cipher.blindIndex("DE00TEST", context: "account.external_id")
    #expect(index.count == 64)
    #expect(index.allSatisfy { "0123456789abcdef".contains($0) })
    #expect(index == cipher.blindIndex("DE00TEST", context: "account.external_id"))
    #expect(index != cipher.blindIndex("DE00OTHER", context: "account.external_id"))
    #expect(index != cipher.blindIndex("DE00TEST", context: "transaction.external_id"))
    #expect(cipher.blindIndex("bc", context: "a") != cipher.blindIndex("c", context: "ab"))
    let other = try AppleFieldCipher(secrets: MemorySecrets())
    #expect(index != other.blindIndex("DE00TEST", context: "account.external_id"))
}

@Test func invalidStoredFieldKeyIsNeverReplaced() throws {
    let secrets = MemorySecrets()
    try secrets.set(AppleFieldCipher.keyName, Data([1, 2, 3]))
    #expect(throws: AppleFieldCipher.Failure.self) { try AppleFieldCipher(secrets: secrets) }
    #expect(try secrets.get(AppleFieldCipher.keyName) == Data([1, 2, 3]))
    #expect(secrets.creationCount == 0)
}
