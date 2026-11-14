import Foundation

/// Versioned schema. Add a new entry to `migrations` for every change; never edit an old one.
enum Schema {
    static let migrations: [String] = [
        // v1
        """
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
            currency TEXT NOT NULL
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
            currency TEXT NOT NULL
        );
        CREATE INDEX holding_snapshot_account_at ON holding_snapshot(account_id, at);
        CREATE TABLE "transaction" (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES account(id),
            external_id TEXT NOT NULL,
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
        """,
        // v2: snapshots get an identity (the sync run), so "latest" no longer means "latest timestamp",
        // an emptied depot is recorded as an empty set, and one run can't store an account's balance twice.
        // Incremental fetches get one cursor per stream, advanced only after a fully successful run.
        """
        ALTER TABLE balance_snapshot ADD COLUMN run_id INTEGER REFERENCES sync_run(id);
        ALTER TABLE holding_snapshot ADD COLUMN run_id INTEGER REFERENCES sync_run(id);
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
        """,
    ]

    static func migrate(_ db: Database) throws {
        let current = try db.query("PRAGMA user_version").first?["user_version"]?.int64 ?? 0
        guard current < migrations.count else { return }
        for (index, sql) in migrations.enumerated() where index >= Int(current) {
            try db.transaction {
                try db.execute(sql)
                try db.execute("PRAGMA user_version = \(index + 1)")
            }
        }
    }
}
