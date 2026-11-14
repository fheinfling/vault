import Foundation
import Security
import Testing

@testable import VaultApple

@Test func schedulerErrorDescriptionsDoNotRecurse() {
    let launchctl = SchedulerError.launchctl(status: 113, command: "bootout")
    #expect("\(launchctl)" == "launchctl bootout failed with status 113")
    let missing = SchedulerError.executableNotFound("/nowhere/vault")
    #expect("\(missing)".contains("/nowhere/vault"))
}

@Test func currentExecutableResolvesToAnAbsoluteExistingFile() throws {
    let url = try LaunchAgentScheduler.resolveCurrentExecutable()
    #expect(url.path.hasPrefix("/"))
    #expect(FileManager.default.isExecutableFile(atPath: url.path))
}

@Test func registerRefusesAMissingExecutable() {
    let scheduler = LaunchAgentScheduler(label: "test.vault.never", executable: URL(fileURLWithPath: "vault"))
    #expect(throws: SchedulerError.self) {
        try scheduler.register(intervalSeconds: 60)
    }
}

@Test func launchAgentDefinitionKeepsEnvironmentAcrossReschedules() throws {
    let logs = URL(fileURLWithPath: "/tmp/vault-logs")
    let executable = URL(fileURLWithPath: "/usr/local/bin/vault")
    let plain = LaunchAgentScheduler(label: "test.vault.never", executable: executable)
    #expect(plain.definition(intervalSeconds: 60, logDirectory: logs, previous: nil)["EnvironmentVariables"] == nil)

    let strict = LaunchAgentScheduler(
        label: "test.vault.never", executable: executable, environment: ["VAULT_REQUIRE_EGRESS": "1"]
    )
    let first = strict.definition(intervalSeconds: 60, logDirectory: logs, previous: nil)
    #expect(first["EnvironmentVariables"] as? [String: String] == ["VAULT_REQUIRE_EGRESS": "1"])

    // A later plain `vault schedule` must not drop the variable, nor one the user added by hand.
    var edited = first
    edited["EnvironmentVariables"] = ["VAULT_REQUIRE_EGRESS": "1", "VAULT_DB": "/tmp/x.db"]
    let again = plain.definition(intervalSeconds: 60, logDirectory: logs, previous: edited)
    #expect(again["EnvironmentVariables"] as? [String: String] == ["VAULT_REQUIRE_EGRESS": "1", "VAULT_DB": "/tmp/x.db"])
}

@Test func egressStatusNamesTheEffectiveGroup() {
    switch EgressConfinement.status() {
    case .confined(let group):
        #expect(group == EgressConfinement.groupName)
    case .unconfined(let reason):
        #expect(reason.contains(EgressConfinement.groupName))
    }
}

@Test func rs256SignatureVerifiesForBothPEMForms() throws {
    // A throwaway RSA key, exported as PKCS#1 and wrapped into both PEM forms the signer accepts.
    var error: Unmanaged<CFError>?
    let attributes: [CFString: Any] = [kSecAttrKeyType: kSecAttrKeyTypeRSA, kSecAttrKeySizeInBits: 2048]
    let key = try #require(SecKeyCreateRandomKey(attributes as CFDictionary, &error))
    let pkcs1 = try #require(SecKeyCopyExternalRepresentation(key, &error)) as Data
    let publicKey = try #require(SecKeyCopyPublicKey(key))

    let pkcs1PEM = pem("RSA PRIVATE KEY", pkcs1)
    // PKCS#8: SEQUENCE { INTEGER 0, AlgorithmIdentifier(rsaEncryption), OCTET STRING pkcs1 }
    let rsaEncryption = Data([0x30, 0x0D, 0x06, 0x09, 0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x01, 0x05, 0x00])
    let inner = Data([0x02, 0x01, 0x00]) + rsaEncryption + der(tag: 0x04, pkcs1)
    let pkcs8PEM = pem("PRIVATE KEY", der(tag: 0x30, inner))

    let signer = AppleSigner()
    let message = Data("enable banking jwt".utf8)
    for pemData in [pkcs1PEM, pkcs8PEM] {
        let signature = try signer.rs256Sign(privateKeyPEM: pemData, message: message)
        let ok = SecKeyVerifySignature(
            publicKey, .rsaSignatureMessagePKCS1v15SHA256, message as CFData, signature as CFData, &error
        )
        #expect(ok)
    }
}

private func pem(_ type: String, _ der: Data) -> Data {
    let body = der.base64EncodedString(options: [.lineLength64Characters])
    return Data("-----BEGIN \(type)-----\n\(body)\n-----END \(type)-----\n".utf8)
}

private func der(tag: UInt8, _ content: Data) -> Data {
    var out = Data([tag])
    let length = content.count
    if length < 0x80 {
        out.append(UInt8(length))
    } else {
        var bytes: [UInt8] = []
        var n = length
        while n > 0 {
            bytes.insert(UInt8(n & 0xFF), at: 0)
            n >>= 8
        }
        out.append(UInt8(0x80 | bytes.count))
        out.append(contentsOf: bytes)
    }
    out.append(content)
    return out
}
