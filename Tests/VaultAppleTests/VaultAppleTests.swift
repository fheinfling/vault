import Foundation
import Testing

@testable import VaultApple

@Test func schedulerErrorDescriptionsDoNotRecurse() {
    let launchctl = SchedulerError.launchctl(status: 113, command: "bootout")
    #expect("\(launchctl)" == "launchctl bootout failed with status 113")
    let missing = SchedulerError.executableNotFound("/nowhere/vault")
    #expect("\(missing)".contains("/nowhere/vault"))
}

@Test func currentExecutableResolvesToAnAbsoluteExistingFile() throws {
    let url = try LaunchAgentScheduler.resolveCurrentExecutable()
    #expect(url.path.hasPrefix("/"))
    #expect(FileManager.default.isExecutableFile(atPath: url.path))
}

@Test func registerRefusesAMissingExecutable() {
    let scheduler = LaunchAgentScheduler(label: "test.vault.never", executable: URL(fileURLWithPath: "vault"))
    #expect(throws: SchedulerError.self) {
        try scheduler.register(intervalSeconds: 60)
    }
}

@Test func launchAgentDefinitionKeepsEnvironmentAcrossReschedules() throws {
    let logs = URL(fileURLWithPath: "/tmp/vault-logs")
    let executable = URL(fileURLWithPath: "/usr/local/bin/vault")
    let plain = LaunchAgentScheduler(label: "test.vault.never", executable: executable)
    #expect(plain.definition(intervalSeconds: 60, logDirectory: logs, previous: nil)["EnvironmentVariables"] == nil)

    let strict = LaunchAgentScheduler(
        label: "test.vault.never", executable: executable, environment: ["VAULT_REQUIRE_EGRESS": "1"]
    )
    let first = strict.definition(intervalSeconds: 60, logDirectory: logs, previous: nil)
    #expect(first["EnvironmentVariables"] as? [String: String] == ["VAULT_REQUIRE_EGRESS": "1"])

    // A later plain `vault schedule` must not drop the variable, nor one the user added by hand.
    var edited = first
    edited["EnvironmentVariables"] = ["VAULT_REQUIRE_EGRESS": "1", "VAULT_DB": "/tmp/x.db"]
    let again = plain.definition(intervalSeconds: 60, logDirectory: logs, previous: edited)
    #expect(again["EnvironmentVariables"] as? [String: String] == ["VAULT_REQUIRE_EGRESS": "1", "VAULT_DB": "/tmp/x.db"])
}

@Test func egressStatusNamesTheEffectiveGroup() {
    switch EgressConfinement.status() {
    case .confined(let group):
        #expect(group == EgressConfinement.groupName)
    case .unconfined(let reason):
        #expect(reason.contains(EgressConfinement.groupName))
    }
}
