// swift-tools-version: 6.0
import PackageDescription

// Rule: this package has no dependencies. CI fails if `dependencies` is ever non-empty.
// Rule: VaultCore, FinTSKit and the connectors import Foundation only, so they build on Linux.
//       Apple frameworks live in VaultApple and the shells, which only exist on Darwin.

var targets: [Target] = [
    .systemLibrary(
        name: "CSQLite",
        path: "Sources/CSQLite",
        pkgConfig: "sqlite3",
        providers: [.apt(["libsqlite3-dev"])]
    ),
    .target(name: "VaultCore", dependencies: ["CSQLite"]),
    .target(name: "FinTSKit"),
    .target(name: "ConnectorDKB", dependencies: ["VaultCore", "FinTSKit"]),
    .target(name: "ConnectorTradeRepublicCSV", dependencies: ["VaultCore"]),
    .target(name: "ConnectorEnableBanking", dependencies: ["VaultCore"]),
    .target(name: "ConnectorScalable", dependencies: ["VaultCore"]),
    .target(name: "ConnectorExchange", dependencies: ["VaultCore"]),
    .testTarget(name: "VaultCoreTests", dependencies: ["VaultCore"]),
]

var products: [Product] = [
    .library(name: "VaultCore", targets: ["VaultCore"]),
    .library(name: "FinTSKit", targets: ["FinTSKit"]),
]

#if canImport(Darwin)
targets += [
    .target(name: "VaultApple", dependencies: ["VaultCore"]),
    .executableTarget(
        name: "vault",
        dependencies: [
            "VaultCore", "VaultApple",
            "ConnectorDKB", "ConnectorTradeRepublicCSV", "ConnectorEnableBanking",
            "ConnectorScalable", "ConnectorExchange",
        ]
    ),
    .testTarget(name: "VaultAppleTests", dependencies: ["VaultApple"]),
    .testTarget(name: "vaultTests", dependencies: ["vault"]),
]
products += [.executable(name: "vault", targets: ["vault"])]
#endif

let package = Package(
    name: "vault",
    platforms: [.macOS(.v14)],
    products: products,
    dependencies: [],
    targets: targets,
    swiftLanguageModes: [.v6]
)
