import Foundation

/// Until the first release, edit this create script and bump the version; older files are refused.
enum Schema {
    private static let version: Int64 = 4
    private static let create = """
        CREATE TABLE connection (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            last_sync TEXT,
            needs_user_action TEXT
        );
        CREATE TABLE account (
            id INTEGER PRIMARY KEY,
            connection_id TEXT NOT NULL REFERENCES connection(id),
            external_id TEXT NOT NULL,
            external_id_sealed TEXT NOT NULL,
            provider TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            iban_masked TEXT,
            currency TEXT NOT NULL,
            is_portfolio INTEGER NOT NULL DEFAULT 0,
            parent_id INTEGER REFERENCES account(id),
            hidden INTEGER NOT NULL DEFAULT 0,
            UNIQUE (connection_id, external_id)
        );
        CREATE TABLE balance_snapshot (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES account(id),
            at TEXT NOT NULL,
            amount TEXT NOT NULL,
            currency TEXT NOT NULL,
            run_id INTEGER NOT NULL REFERENCES sync_run(id)
        );
        CREATE INDEX balance_snapshot_account_at ON balance_snapshot(account_id, at);
        CREATE TABLE holding_snapshot (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES account(id),
            at TEXT NOT NULL,
            isin TEXT,
            name TEXT NOT NULL,
            quantity TEXT NOT NULL,
            price TEXT,
            value TEXT,
            currency TEXT NOT NULL,
            run_id INTEGER NOT NULL REFERENCES sync_run(id)
        );
        CREATE INDEX holding_snapshot_account_at ON holding_snapshot(account_id, at);
        CREATE TABLE "transaction" (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES account(id),
            external_id TEXT NOT NULL,
            external_id_sealed TEXT NOT NULL,
            booked_at TEXT NOT NULL,
            value_at TEXT,
            amount TEXT NOT NULL,
            currency TEXT NOT NULL,
            counterparty TEXT,
            purpose TEXT,
            category TEXT,
            booked INTEGER NOT NULL DEFAULT 1,
            raw_json TEXT,
            UNIQUE (account_id, external_id)
        );
        CREATE INDEX transaction_account_booked ON "transaction"(account_id, booked_at);
        CREATE TABLE activity (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES account(id),
            external_id TEXT NOT NULL,
            external_id_sealed TEXT NOT NULL,
            at TEXT NOT NULL,
            type TEXT NOT NULL,
            isin TEXT,
            quantity TEXT,
            price TEXT,
            fee TEXT,
            tax TEXT,
            amount TEXT NOT NULL,
            currency TEXT NOT NULL,
            raw_json TEXT,
            UNIQUE (account_id, external_id)
        );
        CREATE INDEX activity_account_at ON activity(account_id, at);
        CREATE TABLE sync_run (
            id INTEGER PRIMARY KEY,
            connection_id TEXT NOT NULL REFERENCES connection(id),
            started TEXT NOT NULL,
            finished TEXT,
            ok INTEGER,
            accounts INTEGER NOT NULL DEFAULT 0,
            rows INTEGER NOT NULL DEFAULT 0,
            error TEXT
        );
        CREATE UNIQUE INDEX balance_snapshot_account_run ON balance_snapshot(account_id, run_id);
        CREATE INDEX holding_snapshot_account_run ON holding_snapshot(account_id, run_id);
        CREATE TABLE holding_set (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES account(id),
            run_id INTEGER NOT NULL REFERENCES sync_run(id),
            at TEXT NOT NULL,
            UNIQUE (account_id, run_id)
        );
        CREATE TABLE sync_cursor (
            connection_id TEXT NOT NULL REFERENCES connection(id),
            stream TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (connection_id, stream)
        );
        """

    static func open(path: String) throws -> Database {
        let db = try Database(path: path)
        guard try checkedVersion(db, path: path) == 0 else { return db }
        try db.transaction {
            // Recheck under the write lock: another opener may have created the schema meanwhile.
            if try checkedVersion(db, path: path) == 0 {
                try db.execute(create)
                try db.execute("PRAGMA user_version = \(version)")
            }
        }
        return db
    }

    private static func checkedVersion(_ db: Database, path: String) throws -> Int64 {
        let current = try db.query("PRAGMA user_version").first?["user_version"]?.int64 ?? 0
        guard current == 0 || current == version else { throw UnsupportedSchemaError(version: current, path: path) }
        return current
    }
}

public struct UnsupportedSchemaError: Error, CustomStringConvertible, Sendable {
    public let version: Int64
    public let path: String
    public var description: String {
        "database schema v\(version) is from a pre-release build; delete \(path) and sync again"
    }
}
