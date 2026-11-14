import Testing

@testable import vault

@Test func migrateAcceptsOnlyNoArgumentsOrBackupWithPath() throws {
    #expect(try VaultCLI.migrateBackupPath([]) == nil)
    #expect(try VaultCLI.migrateBackupPath(["--backup", "/safe/pre-v3.db"]) == "/safe/pre-v3.db")
}

@Test(arguments: [
    ["--backup=/safe/pre-v3.db"], ["--bakup", "/safe/pre-v3.db"], ["--backup"], ["--backup", ""],
    ["--backup", "--force"], ["--backup", "/a", "/b"], ["/safe/pre-v3.db"], ["--backup", "/a", "--backup", "/b"],
])
func migrateRejectsAnythingElseBeforeOpeningTheDatabase(arguments: [String]) {
    #expect(throws: CLIError.self) { try VaultCLI.migrateBackupPath(arguments) }
}

@Test func scheduleRejectsUnknownFlags() throws {
    let allowed = VaultCLI.scheduleFlags
    try VaultCLI.expectFlags(["--off", "--require-egress"], allowed: allowed)
    try VaultCLI.expectFlags([], allowed: allowed)
    #expect(throws: CLIError.self) { try VaultCLI.expectFlags(["--require-egres"], allowed: allowed) }
    #expect(throws: CLIError.self) { try VaultCLI.expectFlags(["--of"], allowed: allowed) }
}
