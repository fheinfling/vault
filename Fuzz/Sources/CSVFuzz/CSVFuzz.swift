import Foundation
import VaultCore

private func checkCSV(_ text: String) {
    let detected = CSV.detectDelimiter(text)
    precondition(detected == "," || detected == ";")

    // Exercise both bank-export delimiters regardless of the heuristic, plus TSV.
    for delimiter: Character in [detected, detected == "," ? ";" : ",", "\t"] {
        let records = CSV.parse(text, delimiter: delimiter)
        precondition(records.allSatisfy { !$0.isEmpty })

        let table = CSV.table(text, delimiter: delimiter)
        var headers = records.first ?? []
        if let first = headers.first, first.hasPrefix("\u{FEFF}") {
            headers[0] = String(first.dropFirst())
        }
        precondition(table.headers == headers)
        let nonemptyRecords = records.dropFirst().filter { !$0.allSatisfy(\.isEmpty) }
        precondition(table.rows.count == nonemptyRecords.count)

        // Duplicate headers are allowed: the last column wins, even when padded.
        var lastColumn: [String: Int] = [:]
        for (index, header) in headers.enumerated() { lastColumn[header] = index }
        let keys = Set(headers)
        for (row, record) in zip(table.rows, nonemptyRecords) {
            precondition(Set(row.keys) == keys)
            for (header, index) in lastColumn {
                precondition(row[header] == (index < record.count ? record[index] : ""))
            }
        }
    }
}

@_cdecl("LLVMFuzzerTestOneInput")
public func fuzzCSV(_ data: UnsafePointer<UInt8>, _ size: Int) -> Int32 {
    let bytes = UnsafeBufferPointer(start: data, count: size)
    if let text = String(bytes: bytes, encoding: .utf8) {
        checkCSV(text)
    } else {
        // Invalid UTF-8 must reach the parser too, with replacement characters.
        checkCSV(String(decoding: bytes, as: UTF8.self))
    }
    return 0
}
