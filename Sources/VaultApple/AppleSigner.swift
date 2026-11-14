import CryptoKit
import Foundation
import Security
import VaultCore

/// HMAC and SHA-256 via CryptoKit; RS256 via Security.framework. No OpenSSL, no packages.
public struct AppleSigner: Signer {
    public init() {}

    public func hmacSHA256(key: Data, message: Data) -> Data {
        Data(HMAC<SHA256>.authenticationCode(for: message, using: SymmetricKey(data: key)))
    }

    public func hmacSHA512(key: Data, message: Data) -> Data {
        Data(HMAC<SHA512>.authenticationCode(for: message, using: SymmetricKey(data: key)))
    }

    public func sha256(_ data: Data) -> Data {
        Data(SHA256.hash(data: data))
    }

    public func rs256Sign(privateKeyPEM: Data, message: Data) throws -> Data {
        let der = try PEM.rsaPrivateKeyDER(from: privateKeyPEM)
        let attributes: [CFString: Any] = [
            kSecAttrKeyType: kSecAttrKeyTypeRSA,
            kSecAttrKeyClass: kSecAttrKeyClassPrivate,
        ]
        var error: Unmanaged<CFError>?
        guard let key = SecKeyCreateWithData(der as CFData, attributes as CFDictionary, &error) else {
            throw SignerError.invalidKey(error?.takeRetainedValue().localizedDescription ?? "unreadable RSA key")
        }
        guard let signature = SecKeyCreateSignature(
            key, .rsaSignatureMessagePKCS1v15SHA256, message as CFData, &error
        ) else {
            throw SignerError.signingFailed(error?.takeRetainedValue().localizedDescription ?? "signature failed")
        }
        return signature as Data
    }
}

public enum SignerError: Error, CustomStringConvertible {
    case invalidKey(String)
    case signingFailed(String)
    public var description: String {
        switch self {
        case .invalidKey(let m): return "signer: invalid key: \(m)"
        case .signingFailed(let m): return "signer: \(m)"
        }
    }
}

/// Just enough PEM/DER handling to feed `SecKeyCreateWithData`, which wants PKCS#1 RSA key bytes.
enum PEM {
    static func rsaPrivateKeyDER(from pem: Data) throws -> Data {
        guard let text = String(data: pem, encoding: .utf8) else { throw SignerError.invalidKey("not UTF-8") }
        let lines = text.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }
        guard let begin = lines.firstIndex(where: { $0.hasPrefix("-----BEGIN") }),
              let end = lines.firstIndex(where: { $0.hasPrefix("-----END") }), end > begin
        else { throw SignerError.invalidKey("no PEM block") }
        let body = lines[(begin + 1)..<end].joined()
        guard let der = Data(base64Encoded: body) else { throw SignerError.invalidKey("bad base64") }
        if lines[begin].contains("RSA PRIVATE KEY") {
            return der  // PKCS#1
        }
        if lines[begin].contains("PRIVATE KEY") {
            return try unwrapPKCS8(der)
        }
        throw SignerError.invalidKey("unsupported PEM type: \(lines[begin])")
    }

    /// PKCS#8 is SEQUENCE { INTEGER version, AlgorithmIdentifier, OCTET STRING privateKey }.
    /// The OCTET STRING holds the PKCS#1 bytes we want.
    private static func unwrapPKCS8(_ der: Data) throws -> Data {
        var reader = DERReader(der)
        let outer = try reader.sequence()
        var inner = DERReader(outer)
        _ = try inner.element(expectedTag: 0x02)  // version
        _ = try inner.element(expectedTag: 0x30)  // AlgorithmIdentifier
        return try inner.element(expectedTag: 0x04)  // privateKey
    }

    struct DERReader {
        private let bytes: [UInt8]
        private var offset = 0

        init(_ data: Data) { bytes = [UInt8](data) }

        mutating func sequence() throws -> Data { try element(expectedTag: 0x30) }

        mutating func element(expectedTag: UInt8) throws -> Data {
            guard offset < bytes.count, bytes[offset] == expectedTag else {
                throw SignerError.invalidKey("DER: expected tag \(expectedTag)")
            }
            offset += 1
            let length = try readLength()
            guard offset + length <= bytes.count else { throw SignerError.invalidKey("DER: truncated") }
            let content = Data(bytes[offset..<(offset + length)])
            offset += length
            return content
        }

        private mutating func readLength() throws -> Int {
            guard offset < bytes.count else { throw SignerError.invalidKey("DER: no length") }
            let first = bytes[offset]
            offset += 1
            if first & 0x80 == 0 { return Int(first) }
            let count = Int(first & 0x7F)
            guard count > 0, count <= 4, offset + count <= bytes.count else {
                throw SignerError.invalidKey("DER: bad length")
            }
            var length = 0
            for _ in 0..<count {
                length = (length << 8) | Int(bytes[offset])
                offset += 1
            }
            return length
        }
    }
}
