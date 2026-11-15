# Review instructions

Read by the cross-model reviewers (`cross_review.py`) and by Claude Code
Review. Lives on the protected default branch; a PR never reviews itself under
its own version of this file. Keep it short. Every rule here should change a
review outcome; delete any rule that doesn't.

## What failure looks like in this repo
The vault holds bank credentials and the owner's complete financial history, locally.
- A PIN, API key, token or private key reaches the database, `raw_json`, a log, `print`, an error message, a CLI table, or a test fixture. Secrets exist only in the Keychain behind `SecretStore`.
- A connector talks to any host other than its own provider, or any code path can move money: a payment, order or transfer segment or endpoint. The vault reads; it never pays.
- A re-run sync duplicates, drops or silently rewrites ledger rows: an unstable `externalID`, a wrong upsert key, a balance or holding attached to the wrong account.
- Money arithmetic in `Double`, or a date stored without its time zone, so a booking lands on the wrong day. Amounts are `Decimal` end to end and stored as text; dates are ISO 8601.
- `VaultCore` or any future connector imports an Apple framework, or `Package.swift` gains a dependency. The Linux build and the zero-dependency rule exist to catch exactly this.
- Behavior that differs from the PR's stated intent or acceptance criteria.

## What Important means here
Reserve **Important** for findings that would break behavior, leak data,
corrupt state, or block a rollback, plus any violation of a rule in AGENTS.md /
CLAUDE.md / this file. Style, naming, and refactoring ideas are Nit at most.
Mark bugs the PR did not introduce as **Pre-existing**, unless the PR newly
exposes or worsens them.

## Evidence
- Every finding cites `file:line` in the source. A behavior claim inferred from a
  name is not evidence.
- Every finding states a concrete failure scenario: trigger, then wrong outcome.
- "Missing test" is a finding only when a specific, plausible regression would
  go uncaught.

## Cap the nits
Report at most three Nits per review.

## Do not report
- Anything CI already enforces: formatting, lint, type errors, spelling.
- Generated files, lockfiles, vendored code, snapshots (those are checked by
  the test-integrity detector instead).
- Speculative performance concerns without a realistic input size.
- Requests to add abstraction, comments, or tests for cases that can't happen.

## Always check
- A new or changed connector: its provider host is in `scripts/egress/vault.pf.conf`, it uses read-only credentials where the provider offers them, and every failure maps to a `ConnectorError` case (a `needsUserAction` is how the user gets notified).
- A schema change is a new entry in `Schema.migrations`, never an edit of an old one, and old databases still open.
- Changed `Connector` or `SecretStore` contracts: every connector and adapter still works.
- New behavior is behind a flag (default off) and the PR says how to revert.

## Summary format
Start with a count: "2 important, 1 nit." When there is nothing to fix, say
"0 findings: nothing to fix." plainly. If part of the change could not be
reviewed, say so; an incomplete review is not a clean one.

<!-- Repo-specific rules go below. Format (from OpenAI's Codex guidance):
     flag <behavior>; safe path: <when it is fine>. Test each new rule with one
     change that should trigger it, one safe counterexample, and one unrelated change. -->

## Repo-specific rules
- flag `Double` or `Float` holding an amount, price, quantity or balance; safe path: `Decimal` throughout, `Double` only for chart geometry.
- flag any FinTS segment or HTTP call that initiates a payment, order or transfer (`HKCCS`, `HKCCM`, `HKDSE`, order endpoints); safe path: none. The vault never pays. Scalable orders, if they ever come, are a separate target behind a flag.
- flag `import Security`, `CryptoKit`, `AppKit`, `UserNotifications`, `ServiceManagement` or `AuthenticationServices` outside `Sources/VaultApple` and `Sources/vault`; safe path: add an adapter protocol in `VaultCore` and implement it in `VaultApple`.
- flag a connector that constructs a URL from anything but its own hard-coded provider host; safe path: provider hosts are constants, listed in `scripts/egress/vault.pf.conf`.
