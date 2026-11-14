import Foundation
import Testing

@testable import VaultCore

@Test func parsesQuotesEscapesAndLineEndings() {
    let text = "a,b,c\r\n1,\"x, y\",\"he said \"\"hi\"\"\"\n2,,3"
    let records = CSV.parse(text)
    #expect(records == [["a", "b", "c"], ["1", "x, y", "he said \"hi\""], ["2", "", "3"]])
}

@Test func tableKeysRowsByHeaderAndStripsBOM() {
    let text = "\u{FEFF}Datum;Betrag\n2026-01-02;-12,50\n\n"
    let table = CSV.table(text, delimiter: ";")
    #expect(table.headers == ["Datum", "Betrag"])
    #expect(table.rows.count == 1)
    #expect(table.rows[0]["Betrag"] == "-12,50")
}

@Test func detectsSemicolonDelimiter() {
    #expect(CSV.detectDelimiter("a;b;c\n1;2;3") == ";")
    #expect(CSV.detectDelimiter("a,b\n1,2") == ",")
}

@Test func multilineQuotedFieldStaysOneRecord() {
    let records = CSV.parse("id,note\n1,\"line one\nline two\"")
    #expect(records.count == 2)
    #expect(records[1][1] == "line one\nline two")
}
