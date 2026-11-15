// swift-tools-version: 5.4
import PackageDescription

// Kept separate so ordinary root builds never try to link libFuzzer's main.
// Manifest 5.4 deliberately avoids SwiftPM's 5.5+ testable-executable main
// renaming: libFuzzer supplies main, not CSVFuzz_main. Build with Swift 6.1.
let package = Package(
    name: "VaultFuzz",
    dependencies: [.package(name: "vault", path: "..")],
    targets: [
        .executableTarget(
            name: "CSVFuzz",
            dependencies: [.product(name: "VaultCore", package: "vault")]
        ),
    ]
)
