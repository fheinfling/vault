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

    /// Called inside the v3 schema transaction, so failure leaves the legacy database retryable.
    func migrate(_ db: Database) throws {
        let columns = [
            "account": ["name", "iban_masked", "provider"],
            "balance_snapshot": ["amount"],
            "holding_snapshot": ["name", "isin", "quantity", "price", "value"],
            "transaction": ["amount", "counterparty", "purpose", "category", "raw_json"],
            "activity": ["isin", "quantity", "price", "fee", "tax", "amount", "raw_json"],
        ]
        for (table, names) in columns {
            for row in try db.query("SELECT * FROM \"\(table)\"") {
                var assignments: [String] = []
                var values: [SQLValue] = []
                for name in names {
                    guard let value = row[name]?.string else { continue }
                    let context = "\(table).\(name)"
                    if value.hasPrefix("v1:") {
                        _ = try open(row[name], context)
                    } else {
                        assignments.append("\(name) = ?")
                        values.append(try seal(value, context))
                    }
                }
                if let externalID = row["external_id"]?.string {
                    assignments += ["external_id = ?", "external_id_sealed = ?"]
                    values += [index(externalID, table), try seal(externalID, "\(table).external_id_sealed")]
                }
                if !assignments.isEmpty {
                    try db.run("UPDATE \"\(table)\" SET \(assignments.joined(separator: ", ")) WHERE id = ?",
                               values + [row["id"]!])
                }
            }
        }
    }
}
