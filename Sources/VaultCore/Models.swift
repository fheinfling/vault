import Foundation

// Plain values. Connectors produce these; the ledger stores them.
// Nothing in here may import anything but Foundation.

public enum ConnectionKind: String, Codable, Sendable, CaseIterable {
    case importSeed = "import_seed"
    case dkb
    case tradeRepublicCSV = "tr_csv"
    case enableBanking = "enable_banking"
    case scalable
    case exchange
}

public enum AccountKind: String, Codable, Sendable, CaseIterable {
    case current
    case savings
    case creditCard = "credit_card"
    case depot
    case crypto
    case other
}

public enum ActivityType: String, Codable, Sendable, CaseIterable {
    case buy, sell, dividend, interest, fee, tax
    case transferIn = "transfer_in"
    case transferOut = "transfer_out"
    case other
}

/// An account as reported by a connector. `externalID` must be stable across syncs.
public struct AccountRecord: Codable, Sendable, Equatable {
    public var externalID: String
    public var provider: String
    public var kind: AccountKind
    public var name: String
    public var ibanMasked: String?
    public var currency: String
    public var isPortfolio: Bool
    public var parentExternalID: String?
    public var hidden: Bool

    public init(
        externalID: String, provider: String, kind: AccountKind, name: String,
        ibanMasked: String? = nil, currency: String, isPortfolio: Bool = false,
        parentExternalID: String? = nil, hidden: Bool = false
    ) {
        self.externalID = externalID
        self.provider = provider
        self.kind = kind
        self.name = name
        self.ibanMasked = ibanMasked
        self.currency = currency
        self.isPortfolio = isPortfolio
        self.parentExternalID = parentExternalID
        self.hidden = hidden
    }
}

public struct BalanceRecord: Codable, Sendable, Equatable {
    public var accountExternalID: String
    public var at: Date
    public var amount: Decimal
    public var currency: String

    public init(accountExternalID: String, at: Date, amount: Decimal, currency: String) {
        self.accountExternalID = accountExternalID
        self.at = at
        self.amount = amount
        self.currency = currency
    }
}

public struct HoldingRecord: Codable, Sendable, Equatable {
    public var accountExternalID: String
    public var at: Date
    public var isin: String?
    public var name: String
    public var quantity: Decimal
    public var price: Decimal?
    public var value: Decimal?
    public var currency: String

    public init(
        accountExternalID: String, at: Date, isin: String?, name: String,
        quantity: Decimal, price: Decimal?, value: Decimal?, currency: String
    ) {
        self.accountExternalID = accountExternalID
        self.at = at
        self.isin = isin
        self.name = name
        self.quantity = quantity
        self.price = price
        self.value = value
        self.currency = currency
    }
}

/// A cash movement. `externalID` must be stable so re-imports update instead of duplicate.
public struct TransactionRecord: Codable, Sendable, Equatable {
    public var accountExternalID: String
    public var externalID: String
    public var bookedAt: Date
    public var valueAt: Date?
    public var amount: Decimal
    public var currency: String
    public var counterparty: String?
    public var purpose: String?
    public var category: String?
    public var booked: Bool
    public var raw: String?

    public init(
        accountExternalID: String, externalID: String, bookedAt: Date, valueAt: Date? = nil,
        amount: Decimal, currency: String, counterparty: String? = nil, purpose: String? = nil,
        category: String? = nil, booked: Bool = true, raw: String? = nil
    ) {
        self.accountExternalID = accountExternalID
        self.externalID = externalID
        self.bookedAt = bookedAt
        self.valueAt = valueAt
        self.amount = amount
        self.currency = currency
        self.counterparty = counterparty
        self.purpose = purpose
        self.category = category
        self.booked = booked
        self.raw = raw
    }
}

/// A securities event: buy, sell, dividend and so on.
public struct ActivityRecord: Codable, Sendable, Equatable {
    public var accountExternalID: String
    public var externalID: String
    public var at: Date
    public var type: ActivityType
    public var isin: String?
    public var quantity: Decimal?
    public var price: Decimal?
    public var fee: Decimal?
    public var tax: Decimal?
    public var amount: Decimal
    public var currency: String
    public var raw: String?

    public init(
        accountExternalID: String, externalID: String, at: Date, type: ActivityType,
        isin: String? = nil, quantity: Decimal? = nil, price: Decimal? = nil,
        fee: Decimal? = nil, tax: Decimal? = nil, amount: Decimal, currency: String, raw: String? = nil
    ) {
        self.accountExternalID = accountExternalID
        self.externalID = externalID
        self.at = at
        self.type = type
        self.isin = isin
        self.quantity = quantity
        self.price = price
        self.fee = fee
        self.tax = tax
        self.amount = amount
        self.currency = currency
        self.raw = raw
    }
}

/// What `vault accounts` shows: a stored account with its latest balance.
public struct AccountSummary: Sendable, Equatable {
    public var id: Int64
    public var connectionID: String
    public var externalID: String
    public var provider: String
    public var kind: AccountKind
    public var name: String
    public var currency: String
    public var isPortfolio: Bool
    public var hidden: Bool
    public var balance: Decimal?
    public var balanceAt: Date?
}

public struct SyncRunSummary: Sendable, Equatable {
    public var id: Int64
    public var connectionID: String
    public var started: Date
    public var finished: Date?
    public var ok: Bool?
    public var accounts: Int
    public var rows: Int
    public var error: String?
}
