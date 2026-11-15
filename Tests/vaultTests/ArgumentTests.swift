import Testing

@testable import vault

@Test func scheduleRejectsUnknownFlags() throws {
    let allowed = VaultCLI.scheduleFlags
    try VaultCLI.expectFlags(["--off", "--require-egress"], allowed: allowed)
    try VaultCLI.expectFlags([], allowed: allowed)
    #expect(throws: CLIError.self) { try VaultCLI.expectFlags(["--require-egres"], allowed: allowed) }
    #expect(throws: CLIError.self) { try VaultCLI.expectFlags(["--of"], allowed: allowed) }
}
