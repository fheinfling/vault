import Foundation

// FinTS 3.0 PIN/TAN client, read-only, written from the FinTS 3.0 specification. Foundation only.
// Not derived from python-fints (LGPL); that may only be run as a test oracle (see LICENSE.md, CLAUDE.md).
//
// Scope for the first milestone: dialog initialisation, BPD/UPD, decoupled TAN,
// HKSAL (balances), HKKAZ/HKCAZ (statements), HKWPD (holdings).
// Deliberately absent, now and later: HKCCS and every other payment segment.

public struct FinTSEndpoint: Sendable, Equatable {
    public var url: URL
    public var bankCode: String
    public init(url: URL, bankCode: String) {
        self.url = url
        self.bankCode = bankCode
    }

    public static let dkb = FinTSEndpoint(url: URL(string: "https://fints.dkb.de/fints")!, bankCode: "12030000")
}

public enum FinTSError: Error, Sendable, CustomStringConvertible {
    case notImplemented(String)
    public var description: String {
        switch self {
        case .notImplemented(let what): return "FinTSKit: \(what) is not implemented yet"
        }
    }
}
