# vault

> [!CAUTION]
> **WORK IN PROGRESS – DO NOT USE.**
> This project is unfinished and not functional. None of the bank connectors work yet, nothing here
> has been audited, and the design may change without notice. **Never enter real bank credentials,
> PINs or API keys into this software, and do not rely on it for any financial data.**
> It is published for transparency only.

A local store for every account, balance, holding and transaction, refreshed by its own connectors, using only routes each provider allows. Native Swift, Apple only for now, with a Foundation-only core so an iOS viewer or a Linux sync engine can come later.

Licensed for noncommercial use only: see [LICENSE.md](LICENSE.md).

## Rules
- **Zero package dependencies.** `Package.swift` declares none; CI fails otherwise.
- **`VaultCore`, `FinTSKit` and the connectors import Foundation only.** CI builds them on Linux.
- **Apple frameworks only in `VaultApple` and the shells.**
- **No payment code, anywhere.**
- **Secrets only in the Keychain**, through `SecretStore`.
- **Egress allowlist before the first credential.** See `scripts/egress/`.

## Layout
| Target | Imports | Contents |
|---|---|---|
| `VaultCore` | Foundation | models, ledger over the system SQLite, `Connector` and adapter protocols, sync engine, CSV |
| `FinTSKit` | Foundation | FinTS 3.0 PIN/TAN client, read-only (in progress) |
| `Connector*` | Foundation, VaultCore | one per source: DKB, Trade Republic CSV, Enable Banking, Scalable, exchanges |
| `VaultApple` | Security, CryptoKit, … | Keychain, signer, notifier, launch agent, browser auth |
| `vault` | all of the above | the command-line tool |

## Use
```sh
swift build
swift test
.build/debug/vault accounts
.build/debug/vault sync          # warns: a build run from .build/ is not egress-confined
VAULT_DB=/tmp/test.db .build/debug/vault accounts
```

CSV parser fuzzing runs with libFuzzer on Linux; see [Fuzz/README.md](Fuzz/README.md)
for the local Docker command and crash replay instructions.

## Install, confined
The egress allowlist matches the sync by its effective group, so the real installation is a setgid binary (full steps in `scripts/egress/README.md`):

```sh
swift build -c release
sudo install -m 2755 -o root -g vault .build/release/vault /usr/local/bin/vault
/usr/local/bin/vault egress      # confined: effective group 'vault', the pf allowlist applies
/usr/local/bin/vault schedule    # launch agent, every 4 hours and at login, running this binary
```

`VAULT_REQUIRE_EGRESS=1` makes `vault sync` refuse to run unconfined. Once the allowlist is installed, run `/usr/local/bin/vault schedule --require-egress` to put it in the launch agent's environment. Re-scheduling later keeps it, along with any other `EnvironmentVariables` already in the plist.

Data lives in `~/Library/Application Support/vault/vault.db` (mode 0600). Account names,
providers and masked IBANs; balances; holding names, ISINs, quantities, prices and values;
transaction amounts, counterparties, purposes, categories and raw provider JSON; and activity
ISINs, quantities, prices, fees, taxes, amounts and raw provider JSON are encrypted per field
with AES-256-GCM. Account, transaction and activity external IDs have sealed originals and
keyed blind indexes for idempotent lookups. Stored fields use `v1:` plus base64 and authenticate
the table and column name. Money remains Decimal text inside the encrypted envelope.

The 32-byte master key is created once through `SecretStore` in the login Keychain, under
service `com.franzheinfling.vault`, key `vault.field-key.v1`. Encryption and blind indexes use
separate HKDF-derived subkeys. The key is not stored in the database or its backups. The
command-line build cannot use the data-protection Keychain (it needs a signed app with
entitlements; an ad-hoc-signed binary gets `errSecMissingEntitlement`), so the item lives in
the file-based login keychain: its access list names the vault binary, but it is **not**
device-bound. A copy of the login keychain file plus its password exposes the key. The
`ThisDeviceOnly` accessibility class takes effect once the app shell moves the item to the
data-protection Keychain, a known follow-up that must preserve the existing key. Losing the
Keychain item makes encrypted data unreadable. Processes that can obtain this key can decrypt
the data.

Residual plaintext metadata includes row IDs and relationships, connection IDs/kinds/status
and action messages, account kinds/currencies/flags, all dates (ISO 8601 UTC), activity types,
booked flags, sync runs (including counts and errors), and sync cursors. Blind indexes reveal
equality within a table; row counts, relationships and ciphertext lengths are also visible.
Connection identifiers, action messages and sync errors must not contain credentials or
financial details.

A database from before field encryption (schema v1/v2) is never converted by ordinary
commands; they stop with `database schema v2 predates field encryption`. Convert it explicitly:

    vault migrate --backup ~/vault-pre-v3.db

`--backup` first writes a plaintext copy of the unconverted database (mode 0600, refuses to
overwrite an existing file) and checks it before anything is sealed. That copy is the rollback
path: to go back to a pre-encryption binary, stop vault and put the copy in place of `vault.db`;
reverting code alone cannot decrypt the converted database. Delete the copy once you no longer
need to roll back. Without `--backup` there is no way back to a pre-encryption binary.

The single field-encryption migration, v3 after unchanged v1/v2, adds the sealed-ID columns,
cleanup marker and writer-protection triggers, and seals every legacy row in
one transaction. Fresh databases also start at v3 with these triggers. Conversion then vacuums
SQLite and truncates its WAL to remove old plaintext from the live files. Interrupted cleanup
is retried on the next open; other database readers must close for cleanup to finish. Older copies, backups and filesystem
snapshots are not scrubbed. Do not run an older binary against a converted database: every
write to a ledger table needs a SQL function only the v3 ledger registers, so an older binary's
writes fail with `no such function: vault_v3_writer`, including a sync that was already running
when the conversion committed. `vault migrate` accepts only `--backup PATH` (not `--backup=PATH`)
and rejects anything else before touching the database.
Keep the Keychain item for any encrypted databases you retain.
