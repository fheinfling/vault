import ConnectorDKB
import ConnectorEnableBanking
import ConnectorExchange
import ConnectorScalable
import ConnectorTradeRepublicCSV
import Foundation
import VaultApple
import VaultCore

@main
struct VaultCLI {
    static let usage = """
        usage: vault <command> [options]

          accounts [--all]        list accounts with their latest balance
          balances --account ID   balance history for one account
          holdings --account ID   latest holdings for one depot
          sync [connection ...]   run all connectors, or the named ones
          runs                    recent sync runs
          schedule [--off]        register (or remove) the launch agent, every 4 hours
            [--require-egress]    and set VAULT_REQUIRE_EGRESS=1 in its environment (kept on re-schedule)
          egress                  is this process confined by the egress allowlist?
          migrate [--backup PATH] encrypt a database from before field encryption; with --backup, first
                                  write a verified plaintext copy (needed to roll back to an older vault)
          db-path                 print the database path

        environment:
          VAULT_DB=<path>          overrides the database path
          VAULT_REQUIRE_EGRESS=1   refuse to sync unless the egress allowlist applies to this process
        """

    static func main() async {
        var arguments = Array(CommandLine.arguments.dropFirst())
        guard let command = arguments.first else {
            print(usage)
            exit(2)
        }
        arguments.removeFirst()
        do {
            try await run(command, arguments)
        } catch {
            fputs("vault: \(error)\n", stderr)
            exit(1)
        }
    }

    static func run(_ command: String, _ arguments: [String]) async throws {
        switch command {
        case "db-path":
            print(databasePath)
        case "egress":
            switch EgressConfinement.status() {
            case .confined(let group):
                print("confined: effective group '\(group)', the pf allowlist applies")
            case .unconfined(let reason):
                print("unconfined: \(reason)")
                exit(1)
            }
        case "migrate":
            let backup = try migrateBackupPath(arguments)
            let ledger = try Ledger(
                path: databasePath, cipher: AppleFieldCipher(secrets: KeychainSecretStore()),
                legacy: .convert(backupPath: backup)
            )
            guard let version = ledger.convertedLegacyVersion else {
                print("nothing to migrate: the database has no pre-encryption data.")
                return
            }
            print("encrypted the schema v\(version) database.")
            if let backup {
                print("plaintext pre-encryption backup: \(backup). Delete it once you no longer need to roll back.")
            }
        case "accounts":
            let ledger = try Ledger(path: databasePath, cipher: AppleFieldCipher(secrets: KeychainSecretStore()))
            let accounts = try await ledger.accounts(includeHidden: arguments.contains("--all"))
            if accounts.isEmpty {
                print("no accounts yet. run `vault sync` once a connector is configured.")
                return
            }
            printTable(
                ["ID", "PROVIDER", "KIND", "NAME", "BALANCE", "CCY", "AS OF"],
                accounts.map {
                    [
                        "\($0.id)", $0.provider, $0.kind.rawValue, $0.name,
                        $0.balance.map { "\($0)" } ?? "-", $0.currency,
                        $0.balanceAt.map { $0.formatted(date: .numeric, time: .shortened) } ?? "-",
                    ]
                }
            )
        case "balances":
            let id = try requiredInt64(arguments, "--account")
            let ledger = try Ledger(path: databasePath, cipher: AppleFieldCipher(secrets: KeychainSecretStore()))
            let history = try await ledger.balanceHistory(accountID: id)
            printTable(["AT", "AMOUNT", "CCY"], history.map { [$0.at.formatted(.iso8601), "\($0.amount)", $0.currency] })
        case "holdings":
            let id = try requiredInt64(arguments, "--account")
            let ledger = try Ledger(path: databasePath, cipher: AppleFieldCipher(secrets: KeychainSecretStore()))
            let holdings = try await ledger.latestHoldings(accountID: id)
            printTable(
                ["ISIN", "NAME", "QTY", "PRICE", "VALUE", "CCY"],
                holdings.map {
                    [$0.isin ?? "-", $0.name, "\($0.quantity)", $0.price.map { "\($0)" } ?? "-",
                     $0.value.map { "\($0)" } ?? "-", $0.currency]
                }
            )
        case "sync":
            try checkEgress()
            let ledger = try Ledger(path: databasePath, cipher: AppleFieldCipher(secrets: KeychainSecretStore()))
            let engine = SyncEngine(ledger: ledger, notifier: OSAScriptNotifier())
            var connectors = configuredConnectors()
            if !arguments.isEmpty {
                connectors = connectors.filter { arguments.contains($0.connectionID) }
            }
            if connectors.isEmpty {
                print("no connectors configured.")
                return
            }
            for result in await engine.sync(connectors) {
                let status = result.ok ? "ok" : "FAILED"
                print("\(result.connectionID): \(status), \(result.accounts) accounts, \(result.rows) rows\(result.message.map { ", \($0)" } ?? "")")
            }
        case "runs":
            let ledger = try Ledger(path: databasePath, cipher: AppleFieldCipher(secrets: KeychainSecretStore()))
            let runs = try await ledger.recentSyncRuns()
            printTable(
                ["STARTED", "CONNECTION", "OK", "ACCOUNTS", "ROWS", "ERROR"],
                runs.map {
                    [$0.started.formatted(.iso8601), $0.connectionID, $0.ok.map { $0 ? "yes" : "no" } ?? "-",
                     "\($0.accounts)", "\($0.rows)", $0.error ?? ""]
                }
            )
        case "schedule":
            try expectFlags(arguments, allowed: scheduleFlags)
            let environment = arguments.contains("--require-egress") ? ["VAULT_REQUIRE_EGRESS": "1"] : [:]
            let scheduler = LaunchAgentScheduler(
                executable: try LaunchAgentScheduler.resolveCurrentExecutable(), environment: environment
            )
            if arguments.contains("--off") {
                try scheduler.unregister()
                print("launch agent removed")
            } else {
                try scheduler.register(intervalSeconds: 4 * 3600)
                print("launch agent registered: every 4 hours and at login, running \(scheduler.executable.path)")
            }
        case "help", "--help", "-h":
            print(usage)
        default:
            print(usage)
            exit(2)
        }
    }

    /// Warns when the egress allowlist does not cover this process; refuses with VAULT_REQUIRE_EGRESS=1.
    static func checkEgress() throws {
        guard case .unconfined(let reason) = EgressConfinement.status() else { return }
        if ProcessInfo.processInfo.environment["VAULT_REQUIRE_EGRESS"] == "1" {
            throw CLIError.usage("refusing to sync: \(reason)")
        }
        fputs("vault: warning: \(reason)\n", stderr)
    }

    /// Which connectors exist for this installation. Secrets are looked up by each connector at run time;
    /// a connector with no secret reports `missingSecret` rather than failing to construct.
    static func configuredConnectors() -> [any Connector] {
        let secrets = KeychainSecretStore()
        let signer = AppleSigner()
        let csvFolder = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/vault/imports/trade-republic")
        return [
            TradeRepublicCSVConnector(folder: csvFolder),
            EnableBankingConnector(secrets: secrets, signer: signer),
            DKBConnector(),
            ScalableConnector(secrets: secrets),
        ]
    }

    static var databasePath: String {
        if let override = ProcessInfo.processInfo.environment["VAULT_DB"], !override.isEmpty {
            return override
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/vault/vault.db").path
    }

    static func requiredInt64(_ arguments: [String], _ flag: String) throws -> Int64 {
        guard let index = arguments.firstIndex(of: flag), index + 1 < arguments.count,
              let value = Int64(arguments[index + 1])
        else { throw CLIError.usage("\(flag) <number> is required") }
        return value
    }

    /// Parses `migrate [--backup PATH]` before the database is opened. Anything else, `--backup=PATH` or a
    /// misspelled option included, is rejected: ignoring it would convert without the requested rollback copy.
    static func migrateBackupPath(_ arguments: [String]) throws -> String? {
        if arguments.isEmpty { return nil }
        guard arguments.count == 2, arguments[0] == "--backup", !arguments[1].isEmpty, !arguments[1].hasPrefix("-") else {
            throw CLIError.usage("usage: vault migrate [--backup PATH]")
        }
        return arguments[1]
    }

    static let scheduleFlags: Set = ["--off", "--require-egress"]

    /// Rejects anything but the given flags, so a mistyped option can't silently run the default behavior.
    static func expectFlags(_ arguments: [String], allowed: Set<String>) throws {
        if let unknown = arguments.first(where: { !allowed.contains($0) }) {
            throw CLIError.usage("unknown option \(unknown); expected \(allowed.sorted().joined(separator: ", "))")
        }
    }

    static func printTable(_ headers: [String], _ rows: [[String]]) {
        let all = [headers] + rows
        let widths = headers.indices.map { column in all.map { $0[column].count }.max() ?? 0 }
        for row in all {
            print(row.enumerated().map { $0.element.padding(toLength: widths[$0.offset], withPad: " ", startingAt: 0) }
                .joined(separator: "  "))
        }
    }
}

enum CLIError: Error, CustomStringConvertible {
    case usage(String)
    var description: String {
        switch self {
        case .usage(let m): return m
        }
    }
}
