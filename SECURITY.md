# Security policy

vault handles bank credentials and financial data, so security reports are welcome.

## Reporting a vulnerability

Please report it **privately** through GitHub: open the repository's **Security** tab and choose
**Report a vulnerability**. Do not open a public issue for security problems.

You can expect an acknowledgement within a few days. There is no bug bounty.

## Scope

Everything in this repository, in particular: anything that could expose credentials or financial
data (Keychain use, the database, logs), any code path that could move money (the project is
read-only by design), the egress allowlist in `scripts/egress/`, and the CI and review tooling.

## Supported versions

Only the current `main` branch is supported.
