import Foundation

/// A small RFC 4180 parser: quoted fields, doubled quotes, CR/LF/CRLF line ends, configurable delimiter.
public enum CSV {
    public struct Table: Sendable, Equatable {
        public var headers: [String]
        public var rows: [[String: String]]

        public init(headers: [String], rows: [[String: String]]) {
            self.headers = headers
            self.rows = rows
        }
    }

    public static func parse(_ text: String, delimiter: Character = ",") -> [[String]] {
        var records: [[String]] = []
        var fields: [String] = []
        var field = ""
        var inQuotes = false
        var iterator = text.makeIterator()
        var pending: Character? = nil

        func next() -> Character? {
            if let p = pending {
                pending = nil
                return p
            }
            return iterator.next()
        }

        while let c = next() {
            if inQuotes {
                if c == "\"" {
                    if let lookahead = next() {
                        if lookahead == "\"" {
                            field.append("\"")
                        } else {
                            inQuotes = false
                            pending = lookahead
                        }
                    } else {
                        inQuotes = false
                    }
                } else {
                    field.append(c)
                }
                continue
            }
            switch c {
            case "\"":
                inQuotes = true
            case delimiter:
                fields.append(field)
                field = ""
            case "\r":
                if let lookahead = next(), lookahead != "\n" { pending = lookahead }
                fields.append(field)
                records.append(fields)
                fields = []
                field = ""
            case "\n", "\r\n":  // Swift treats CRLF as one Character
                fields.append(field)
                records.append(fields)
                fields = []
                field = ""
            default:
                field.append(c)
            }
        }
        if !field.isEmpty || !fields.isEmpty {
            fields.append(field)
            records.append(fields)
        }
        return records
    }

    /// First record as headers, the rest keyed by header. Short rows are padded with empty strings.
    public static func table(_ text: String, delimiter: Character = ",") -> Table {
        var records = parse(text, delimiter: delimiter)
        guard !records.isEmpty else { return Table(headers: [], rows: []) }
        var headers = records.removeFirst()
        if let first = headers.first, first.hasPrefix("\u{FEFF}") {
            headers[0] = String(first.dropFirst())
        }
        let rows: [[String: String]] = records.compactMap { record in
            if record.allSatisfy(\.isEmpty) { return nil }
            var row: [String: String] = [:]
            for (index, header) in headers.enumerated() {
                row[header] = index < record.count ? record[index] : ""
            }
            return row
        }
        return Table(headers: headers, rows: rows)
    }

    /// Guesses the delimiter from the first line: the more frequent of `;` and `,`.
    public static func detectDelimiter(_ text: String) -> Character {
        let firstLine = text.split(whereSeparator: \.isNewline).first.map(String.init) ?? ""
        let semicolons = firstLine.filter { $0 == ";" }.count
        let commas = firstLine.filter { $0 == "," }.count
        return semicolons > commas ? ";" : ","
    }
}
