# vault

> [!CAUTION]
> **WORK IN PROGRESS – DO NOT USE.**
> This project is unfinished and not functional. No bank connectors are built yet, nothing here
> has been audited, and the design may change without notice. **Never enter real bank credentials,
> PINs or API keys into this software, and do not rely on it for any financial data.**
> It is published for transparency only.

A local store for every account, balance, holding and transaction, refreshed by its own connectors, using only routes each provider allows. Native Swift, Apple only for now, with a Foundation-only core so an iOS viewer or a Linux sync engine can come later.

Licensed for noncommercial use only: see [LICENSE.md](LICENSE.md).

## Rules
- **Zero package dependencies.** `Package.swift` declares none; CI fails otherwise.
- **`VaultCore` and future connectors import Foundation only.** CI builds the core on Linux.
- **Apple frameworks only in `VaultApple` and the shells.**
- **No payment code, anywhere.**
- **Secrets only in the Keychain**, through `SecretStore`.
- **Egress allowlist before the first credential.** See `scripts/egress/`.

## Layout
| Target | Imports | Contents |
|---|---|---|
| `VaultCore` | Foundation | models, ledger over the system SQLite, `Connector` and adapter protocols, sync engine, CSV |
| `VaultApple` | Security, CryptoKit, … | Keychain, field cipher, notifier, launch agent, egress check |
| `vault` | all of the above | the command-line tool |

Connectors arrive one per source, in the PR that implements them.

## Use
```sh
swift build
swift test
.build/debug/vault accounts
.build/debug/vault sync          # no connectors are built yet; exits 0
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

`VAULT_REQUIRE_EGRESS=1` makes `vault sync` refuse to run connectors unconfined. Once the allowlist is installed, run `/usr/local/bin/vault schedule --require-egress` to put it in the launch agent's environment. Re-scheduling later keeps it, along with any other `EnvironmentVariables` already in the plist.

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

The schema is created fresh; pre-release databases with older schema versions must be deleted and synced again.
