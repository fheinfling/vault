import Foundation
import VaultCore

/// Posts a macOS notification from a command-line process.
///
/// `UserNotifications` needs an app bundle, so the CLI goes through `osascript`, an Apple tool.
/// The app shell (`VaultApp`) replaces this with `UNUserNotificationCenter`.
public struct OSAScriptNotifier: Notifier {
    public init() {}

    public func notify(title: String, body: String) async {
        let script = "display notification \(quoted(body)) with title \(quoted(title))"
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", script]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try? process.run()
        process.waitUntilExit()
    }

    private func quoted(_ s: String) -> String {
        "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }
}
