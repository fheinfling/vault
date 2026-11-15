import Foundation
import Testing

@testable import VaultCore

@Test func freshDatabaseCreatesCurrentSchema() throws {
    let path = NSTemporaryDirectory() + "vault-schema-\(UUID().uuidString).db"
    defer {
        for suffix in ["", "-wal", "-shm"] { try? FileManager.default.removeItem(atPath: path + suffix) }
    }
    _ = try Ledger(path: path, cipher: TestFieldCipher())
    let db = try Database(path: path)
    #expect(try db.query("PRAGMA user_version").first?["user_version"]?.int64 == 4)
    let tables = try db.query("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").compactMap { $0["name"]?.string }
    #expect(tables == [
        "account", "activity", "balance_snapshot", "connection", "holding_set", "holding_snapshot",
        "sync_cursor", "sync_run", "transaction",
    ])
    #expect(try db.query("SELECT name FROM sqlite_master WHERE type = 'trigger'").isEmpty)
    for table in ["account", "transaction", "activity"] {
        #expect(try db.query("PRAGMA table_info('\(table)')").contains { $0["name"]?.string == "external_id_sealed" })
    }
    for table in ["balance_snapshot", "holding_snapshot"] {
        #expect(try db.query("PRAGMA table_info('\(table)')").contains { $0["name"]?.string == "run_id" })
    }
    let indexes = try db.query("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL ORDER BY name")
        .compactMap { $0["name"]?.string }
    #expect(indexes == [
        "activity_account_at", "balance_snapshot_account_at", "balance_snapshot_account_run",
        "holding_snapshot_account_at", "holding_snapshot_account_run", "transaction_account_booked",
    ])
    #expect(try FileManager.default.attributesOfItem(atPath: path)[.posixPermissions] as? Int == 0o600)
}

@Test(arguments: [1, 2, 3, 5])
func unsupportedSchemaIsRefusedAndDataKept(version: Int) throws {
    let path = NSTemporaryDirectory() + "vault-schema-\(UUID().uuidString).db"
    defer {
        for suffix in ["", "-wal", "-shm"] { try? FileManager.default.removeItem(atPath: path + suffix) }
    }
    do {
        let db = try Database(path: path)
        try db.execute("CREATE TABLE retained (value TEXT); INSERT INTO retained VALUES ('keep me')")
        try db.execute("PRAGMA user_version = \(version)")
    }
    #expect(throws: UnsupportedSchemaError.self) { try Ledger(path: path, cipher: TestFieldCipher()) }
    let db = try Database(path: path)
    #expect(try db.query("PRAGMA user_version").first?["user_version"]?.int64 == Int64(version))
    #expect(try db.query("SELECT value FROM retained").first?["value"]?.string == "keep me")
    #expect(try db.query("SELECT name FROM sqlite_master WHERE name = 'account'").isEmpty)
}

@Test func currentSchemaOpensWhileAnotherConnectionWrites() throws {
    let path = NSTemporaryDirectory() + "vault-schema-\(UUID().uuidString).db"
    defer {
        for suffix in ["", "-wal", "-shm"] { try? FileManager.default.removeItem(atPath: path + suffix) }
    }
    _ = try Ledger(path: path, cipher: TestFieldCipher())
    let writer = try Database(path: path)
    try writer.transaction {
        try writer.run("INSERT INTO connection (id, kind) VALUES ('test', 'exchange')")
        _ = try Ledger(path: path, cipher: TestFieldCipher())
    }
}
