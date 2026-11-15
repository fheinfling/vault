import Foundation
import Testing

private final class SyncTestBundle: NSObject {}

@Test(arguments: [[], ["dkb"]])
func syncWithoutConnectorsSucceedsWithoutOpeningDatabase(connections: [String]) throws {
    let bundle = Bundle(for: SyncTestBundle.self)
    let process = Process()
    process.executableURL = bundle.bundleURL.deletingLastPathComponent().appendingPathComponent("vault")
    process.arguments = ["sync"] + connections
    var environment = ProcessInfo.processInfo.environment
    environment["VAULT_REQUIRE_EGRESS"] = "1"
    environment["VAULT_DB"] = "/dev/null/vault.db"
    process.environment = environment
    let output = Pipe()
    let errors = Pipe()
    process.standardOutput = output
    process.standardError = errors

    try process.run()
    process.waitUntilExit()

    #expect(process.terminationStatus == 0)
    #expect(String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        == "no connectors are built yet.\n")
    #expect(errors.fileHandleForReading.readDataToEndOfFile().isEmpty)
}
