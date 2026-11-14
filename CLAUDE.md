@AGENTS.md

<!-- Keep this file short: only what Claude can't work out from the code.
     Replace the placeholders, delete what doesn't apply. -->

## Verify your work
- Fast checks: `code_quality/scripts/quality_gate.sh fast` (also runs automatically when you finish a turn)
- Full gate: `code_quality/scripts/quality_gate.sh full`
- Single test: `swift test --filter <testFunctionName>` (Swift Testing; for example `swift test --filter upsertAccountsIsIdempotent`)
- Linux build of the core: `.github/workflows/ci.yml` does it in CI; locally, `docker run --rm -v "$PWD":/src -w /src swift:6.1 sh -c 'apt-get update -qq && apt-get install -y -qq libsqlite3-dev pkg-config && swift test'`
- Review loop before PR: `code_quality/scripts/review_loop.sh --intent "<task>"`

## Where the code misleads
- Every `Connector*` target is a stub that throws `ConnectorError.notImplemented`. A `vault sync` that reports FAILED for them is expected, not a bug, until that connector is built.
- `Package.swift` wraps `VaultApple` and the `vault` executable in `#if canImport(Darwin)`. On Linux those targets don't exist; that is deliberate, so the core's Linux build is a tripwire against Apple imports.
- `OSAScriptNotifier` and `LaunchAgentScheduler` are command-line stand-ins for `UserNotifications` and `SMAppService`, which need an app bundle. The app shell replaces them; don't extend them.
- `Database` is not thread-safe and is not `Sendable`. Only the `Ledger` actor owns one. Don't hand it out.

## Decisions not visible in the code
- Zero package dependencies, ever. `scripts/check-no-dependencies.sh` enforces it. Apple frameworks are allowed only in `Sources/VaultApple` and `Sources/vault`.
- No payment code, ever. FinTSKit implements read segments only. The vault reads accounts; it never moves money.
- Secrets live only in the Keychain, through `SecretStore`. Connectors receive the store, not the secret, and look up their own key at run time.
- Amounts are `Decimal`, stored as text. Dates are ISO 8601 strings in UTC. Both choices are so SQLite never rounds money or shifts a booking day.
- Field encryption is mandatory at the ledger boundary, including the CLI; the requested security fix has no plaintext opt-out. Core callers inject a `FieldCipher`; Linux implementations must provide authenticated encryption, never the test fake.
- The field master key lives only behind `SecretStore` as `vault.field-key.v1` (Keychain service `com.franzheinfling.vault`). The CLI has no entitlements, so this is the file-based login keychain and is not device-bound; `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` only takes effect once the signed app shell switches to the data-protection Keychain (a known follow-up; preserve the existing key when it does). Atomic create-if-absent prevents concurrent first-use CLI processes from choosing different keys. Separate HKDF subkeys isolate AES-GCM encryption and HMAC blind indexes. Key loss means data loss; a database backup alone cannot recover it.
- Names/providers/IBANs, all monetary and holding details, transaction counterparties/purposes/categories, raw provider JSON and external IDs are encrypted. External ID blind indexes preserve equality and upsert identity. Table/column associated data prevents swapping columns, but does not prevent replay within the same column or authenticate row relationships.
- Residual plaintext: row IDs/relationships; connection IDs/kinds/status/action messages; account kinds/currencies/flags; all dates; activity types; booked flags; sync runs/counts/errors; and cursors. Blind-index equality, row counts and ciphertext lengths remain visible. Do not put credentials or financial details in connection identifiers or operational messages/errors.
- The single field-encryption migration, v3 after unchanged v1/v2, adds sealed-ID columns and writer-protection triggers and atomically seals legacy rows, then vacuums and truncates the WAL, with a persistent cleanup marker for retries. Converting an existing v1/v2 database is opt-in (`Ledger(legacy: .convert)`, CLI `vault migrate [--backup PATH]`); every other open throws `LegacyDatabaseError` and leaves the file untouched. `--backup` writes and verifies a 0600 plaintext copy first, which is the only rollback path. A fresh database starts encrypted. The `v1:` prefix is reserved for envelopes; invalid envelopes fail closed. Old backups/filesystem snapshots are outside this cleanup. No pre-v3 binary may write a migrated database; v3's `*_requires_v3` BEFORE INSERT triggers make such writes abort. Retain the key for retained encrypted copies.
- Every record has a stable `externalID`; the ledger upserts on it so a re-run never duplicates. Some providers change amounts after booking, so `SyncEngine` re-fetches a 14-day overlap.
- Errors from connectors are `ConnectorError` cases. `needsUserAction` is the one that reaches the user as a notification (DKB app approval, a new Trade Republic CSV, an expired consent).
- The egress allowlist (`scripts/egress/`) must be in place before the first real credential goes into the Keychain.
- The plan and the reasoning behind the connector choices are kept outside this repo. python-fints may be kept under `reference/` as a test oracle, never built into the product.
- License: PolyForm Noncommercial 1.0.0 (`LICENSE.md`). FinTSKit must not be a port of python-fints: python-fints is LGPL, and an LGPL-derived part could not carry the noncommercial restriction. Write FinTSKit from the FinTS 3.0 specification; python-fints may be run as a test oracle, never copied or translated.
- Flags: none yet. New user-visible behavior gets one when the app shell exists; until then, the CLI is the only surface and the revert is `git revert`, except for schema v3 (field encryption), whose revert also needs the pre-v3 copy written by `vault migrate --backup`.
