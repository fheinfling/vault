import CSQLite
import Foundation

public enum SQLValue: Sendable, Equatable {
    case null
    case integer(Int64)
    case real(Double)
    case text(String)
    case blob(Data)

    public var int64: Int64? {
        if case .integer(let v) = self { return v }
        return nil
    }
    public var string: String? {
        if case .text(let v) = self { return v }
        return nil
    }
    public var bool: Bool { (int64 ?? 0) != 0 }
}

public struct DatabaseError: Error, CustomStringConvertible, Sendable {
    public let code: Int32
    public let message: String
    public var description: String { "sqlite error \(code): \(message)" }
}

private let sqliteTransient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

/// A thin wrapper over the system SQLite. Not thread-safe by itself; `Ledger` (an actor) owns the only instance.
final class Database {
    private var handle: OpaquePointer?

    init(path: String) throws {
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
        var h: OpaquePointer?
        let rc = sqlite3_open_v2(path, &h, flags, nil)
        guard rc == SQLITE_OK, let opened = h else {
            let message = h.map { String(cString: sqlite3_errmsg($0)) } ?? "cannot open"
            sqlite3_close_v2(h)
            throw DatabaseError(code: rc, message: message)
        }
        handle = opened
        if path != ":memory:" {
            chmod(path, 0o600)
        }
        // First, so the pragmas below wait out another process's transient lock instead of failing.
        try execute("PRAGMA busy_timeout=5000")
        try execute("PRAGMA journal_mode=WAL")
        try execute("PRAGMA secure_delete=ON")
        try execute("PRAGMA foreign_keys=ON")
    }

    deinit {
        sqlite3_close_v2(handle)
    }

    func execute(_ sql: String) throws {
        var errorMessage: UnsafeMutablePointer<CChar>?
        let rc = sqlite3_exec(handle, sql, nil, nil, &errorMessage)
        guard rc == SQLITE_OK else {
            let message = errorMessage.map { String(cString: $0) } ?? "unknown"
            sqlite3_free(errorMessage)
            throw DatabaseError(code: rc, message: message)
        }
    }

    /// Runs a statement that returns no rows. Returns the number of rows changed.
    @discardableResult
    func run(_ sql: String, _ params: [SQLValue] = []) throws -> Int {
        let statement = try prepare(sql, params)
        defer { sqlite3_finalize(statement) }
        let rc = sqlite3_step(statement)
        guard rc == SQLITE_DONE || rc == SQLITE_ROW else {
            throw DatabaseError(code: rc, message: String(cString: sqlite3_errmsg(handle)))
        }
        return Int(sqlite3_changes(handle))
    }

    func query(_ sql: String, _ params: [SQLValue] = []) throws -> [[String: SQLValue]] {
        let statement = try prepare(sql, params)
        defer { sqlite3_finalize(statement) }
        let columnCount = Int(sqlite3_column_count(statement))
        let names: [String] = (0..<columnCount).map { String(cString: sqlite3_column_name(statement, Int32($0))) }
        var rows: [[String: SQLValue]] = []
        while true {
            let rc = sqlite3_step(statement)
            if rc == SQLITE_DONE { break }
            guard rc == SQLITE_ROW else {
                throw DatabaseError(code: rc, message: String(cString: sqlite3_errmsg(handle)))
            }
            var row: [String: SQLValue] = [:]
            for i in 0..<columnCount {
                row[names[i]] = column(statement, Int32(i))
            }
            rows.append(row)
        }
        return rows
    }

    var lastInsertRowID: Int64 { sqlite3_last_insert_rowid(handle) }

    func transaction<T>(_ body: () throws -> T) throws -> T {
        try execute("BEGIN IMMEDIATE")
        do {
            let result = try body()
            try execute("COMMIT")
            return result
        } catch {
            try? execute("ROLLBACK")
            throw error
        }
    }

    // MARK: - Private

    private func prepare(_ sql: String, _ params: [SQLValue]) throws -> OpaquePointer {
        var statement: OpaquePointer?
        let rc = sqlite3_prepare_v2(handle, sql, -1, &statement, nil)
        guard rc == SQLITE_OK, let prepared = statement else {
            throw DatabaseError(code: rc, message: String(cString: sqlite3_errmsg(handle)))
        }
        for (offset, value) in params.enumerated() {
            let index = Int32(offset + 1)
            let bindResult: Int32
            switch value {
            case .null:
                bindResult = sqlite3_bind_null(prepared, index)
            case .integer(let v):
                bindResult = sqlite3_bind_int64(prepared, index, v)
            case .real(let v):
                bindResult = sqlite3_bind_double(prepared, index, v)
            case .text(let v):
                bindResult = sqlite3_bind_text(prepared, index, v, -1, sqliteTransient)
            case .blob(let v):
                if v.isEmpty {
                    bindResult = sqlite3_bind_zeroblob(prepared, index, 0)
                } else {
                    bindResult = v.withUnsafeBytes {
                        sqlite3_bind_blob(prepared, index, $0.baseAddress, Int32(v.count), sqliteTransient)
                    }
                }
            }
            guard bindResult == SQLITE_OK else {
                sqlite3_finalize(prepared)
                throw DatabaseError(code: bindResult, message: "bind failed at \(index)")
            }
        }
        return prepared
    }

    private func column(_ statement: OpaquePointer, _ index: Int32) -> SQLValue {
        switch sqlite3_column_type(statement, index) {
        case SQLITE_INTEGER:
            return .integer(sqlite3_column_int64(statement, index))
        case SQLITE_FLOAT:
            return .real(sqlite3_column_double(statement, index))
        case SQLITE_TEXT:
            return .text(String(cString: sqlite3_column_text(statement, index)))
        case SQLITE_BLOB:
            let count = Int(sqlite3_column_bytes(statement, index))
            guard count > 0, let bytes = sqlite3_column_blob(statement, index) else { return .blob(Data()) }
            return .blob(Data(bytes: bytes, count: count))
        default:
            return .null
        }
    }
}
