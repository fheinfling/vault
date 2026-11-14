import Foundation
import VaultCore

/// A per-user launchd agent that runs `vault sync` on an interval and at login.
///
/// `SMAppService` needs an app bundle, so the CLI writes a plist to `~/Library/LaunchAgents`
/// and registers it with `launchctl`. The app shell switches to `SMAppService.agent(plistName:)`.
public struct LaunchAgentScheduler: Scheduler {
    public let label: String
    public let executable: URL
    /// Variables for the agent's environment, for example `VAULT_REQUIRE_EGRESS=1`.
    public let environment: [String: String]

    public init(label: String = "com.franzheinfling.vault.sync", executable: URL, environment: [String: String] = [:]) {
        self.label = label
        self.executable = executable
        self.environment = environment
    }

    /// The running binary's real, absolute path. `argv[0]` is not that: when the tool is started
    /// through `PATH` it is a bare name, and resolving it against the working directory points
    /// the launch agent at a file that does not exist.
    public static func resolveCurrentExecutable() throws -> URL {
        guard let url = Bundle.main.executableURL?.resolvingSymlinksInPath() else {
            throw SchedulerError.executableNotFound("the running executable's path is unknown")
        }
        guard url.path.hasPrefix("/"), FileManager.default.isExecutableFile(atPath: url.path) else {
            throw SchedulerError.executableNotFound(url.path)
        }
        return url
    }

    private var plistURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/\(label).plist")
    }

    public func register(intervalSeconds: Int) throws {
        guard executable.path.hasPrefix("/"), FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw SchedulerError.executableNotFound(executable.path)
        }
        let logDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/vault")
        try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
        let previous = (try? Data(contentsOf: plistURL)).flatMap {
            try? PropertyListSerialization.propertyList(from: $0, format: nil) as? [String: Any]
        }
        let plist = definition(intervalSeconds: intervalSeconds, logDirectory: logDirectory, previous: previous)
        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try FileManager.default.createDirectory(
            at: plistURL.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try data.write(to: plistURL, options: .atomic)
        if isRegistered() {
            _ = launchctl(["bootout", domain, plistURL.path])  // replace the loaded definition
        }
        let status = launchctl(["bootstrap", domain, plistURL.path])
        guard status == 0 else { throw SchedulerError.launchctl(status: status, command: "bootstrap") }
    }

    /// The launch agent's plist. Re-registering rewrites the whole file, so the `EnvironmentVariables` of the
    /// previous definition are carried over: a variable set by hand, or by an earlier `--require-egress`,
    /// survives a later `vault schedule`. `environment` wins where both name the same variable.
    func definition(intervalSeconds: Int, logDirectory: URL, previous: [String: Any]?) -> [String: Any] {
        var plist: [String: Any] = [
            "Label": label,
            "ProgramArguments": [executable.path, "sync"],
            "RunAtLoad": true,
            "StartInterval": intervalSeconds,
            "StandardOutPath": logDirectory.appendingPathComponent("sync.log").path,
            "StandardErrorPath": logDirectory.appendingPathComponent("sync.err").path,
            "ProcessType": "Background",
        ]
        let kept = previous?["EnvironmentVariables"] as? [String: String] ?? [:]
        let variables = kept.merging(environment) { _, new in new }
        if !variables.isEmpty {
            plist["EnvironmentVariables"] = variables
        }
        return plist
    }

    public func unregister() throws {
        if isRegistered() {
            let status = launchctl(["bootout", domain, plistURL.path])
            guard status == 0 else { throw SchedulerError.launchctl(status: status, command: "bootout") }
        }
        try? FileManager.default.removeItem(at: plistURL)
    }

    public func isRegistered() -> Bool {
        launchctl(["print", "\(domain)/\(label)"]) == 0
    }

    private var domain: String { "gui/\(getuid())" }

    private func launchctl(_ arguments: [String]) -> Int32 {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            return -1
        }
        process.waitUntilExit()
        return process.terminationStatus
    }
}

public enum SchedulerError: Error, CustomStringConvertible, Equatable {
    case launchctl(status: Int32, command: String)
    case executableNotFound(String)

    public var description: String {
        switch self {
        case .launchctl(let status, let command):
            return "launchctl \(command) failed with status \(status)"
        case .executableNotFound(let path):
            return "no executable at \(path); run the installed binary by its full path"
        }
    }
}
