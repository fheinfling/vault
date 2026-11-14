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

## Install, confined
The egress allowlist matches the sync by its effective group, so the real installation is a setgid binary (full steps in `scripts/egress/README.md`):

```sh
swift build -c release
sudo install -m 2755 -o root -g vault .build/release/vault /usr/local/bin/vault
/usr/local/bin/vault egress      # confined: effective group 'vault', the pf allowlist applies
/usr/local/bin/vault schedule    # launch agent, every 4 hours and at login, running this binary
```

`VAULT_REQUIRE_EGRESS=1` makes `vault sync` refuse to run unconfined. Once the allowlist is installed, run `/usr/local/bin/vault schedule --require-egress` to put it in the launch agent's environment. Re-scheduling later keeps it, along with any other `EnvironmentVariables` already in the plist.

Data lives in `~/Library/Application Support/vault/vault.db` (mode 0600).
