# CSV fuzzing (Linux)

`CSVFuzz` uses the swift.org toolchain's libFuzzer and AddressSanitizer to exercise
VaultCore's delimiter detection, parsing, and table conversion. It accepts arbitrary
bytes: valid UTF-8 is decoded strictly; invalid UTF-8 is decoded with replacement
characters. Each input is tried with comma, semicolon, and tab delimiters. Assertions
check nonempty record arrays, BOM handling, empty-row removal, header keys, padding,
and duplicate-header last-column behavior. Malformed CSV is intentionally accepted.

The separate Swift package references only the local root package, whose package
dependency list remains empty. Normal root `swift build` / `swift test` are unchanged.
Its manifest uses tools version 5.4 to avoid SwiftPM's newer executable-entry-point
renaming, allowing libFuzzer to provide `main`; the compiler is still Swift 6.1.
Both VaultCore and the harness are built with coverage instrumentation and ASan.
Apple's toolchain does not supply libFuzzer; on macOS use Docker:

```sh
docker run --rm -v "$PWD":/src -w /src swift:6.1 bash -c \
  'apt-get update -qq && apt-get install -y -qq libsqlite3-dev pkg-config >/dev/null && ./scripts/fuzz.sh 60'
```

On Linux with Swift 6.1 and SQLite development headers installed, run
`./scripts/fuzz.sh [seconds]` (default 60, range 1–86400). The fuzzing budget excludes
compilation; libFuzzer checks it between inputs, with a 10-second per-input timeout.
Inputs are limited to 4096 bytes to keep CI fast. CI runs the `fuzz` job for 60 seconds
on pushes and pull requests. All checked-in seeds are synthetic, including the empty
file, BOM/semicolon export, multiline quoted text, and uneven/duplicate columns.

Build output, the growing working corpus, and failure artifacts live in ignored
`Fuzz/.build/`. Failures exit nonzero; libFuzzer prints the reproducer path. Replay
one in the same Linux environment using the built binary:

```sh
"$(swift build --package-path Fuzz --scratch-path Fuzz/.build --show-bin-path)/CSVFuzz" \
  Fuzz/.build/artifacts/crash-<hash>
```

No application behavior or storage changes. To revert, remove `Fuzz/`,
`scripts/fuzz.sh`, the `fuzz` CI job, and the README link.
