import Foundation

/// Platform-provided authenticated encryption. Context binds each value to its table and column.
public protocol FieldCipher: Sendable {
    func seal(_ plaintext: Data, context: String) throws -> Data
    func open(_ sealed: Data, context: String) throws -> Data
    /// Deterministic keyed MAC, encoded as hex, for equality lookups.
    func blindIndex(_ value: String, context: String) -> String
}

enum FieldEncodingError: Error {
    case invalidEnvelope
}

struct FieldStorage {
    let cipher: any FieldCipher

    func seal(_ value: String?, _ context: String) throws -> SQLValue {
        guard let value else { return .null }
        return .text("v1:" + (try cipher.seal(Data(value.utf8), context: context)).base64EncodedString())
    }

    func open(_ value: SQLValue?, _ context: String) throws -> String? {
        guard let value, value != .null else { return nil }
        guard let text = value.string, text.hasPrefix("v1:"),
              let data = Data(base64Encoded: String(text.dropFirst(3))),
              let plaintext = String(data: try cipher.open(data, context: context), encoding: .utf8)
        else { throw FieldEncodingError.invalidEnvelope }
        return plaintext
    }

    func index(_ value: String, _ table: String) -> SQLValue {
        .text(cipher.blindIndex(value, context: "\(table).external_id"))
    }
}
