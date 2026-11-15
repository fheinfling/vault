# Review instructions

Read by the cross-model reviewers (`cross_review.py`) and by Claude Code
Review. Lives on the protected default branch; a PR never reviews itself under
its own version of this file. Keep it short. Every rule here should change a
review outcome; delete any rule that doesn't.

## What failure looks like in this repo
<!-- Replace with 3–6 lines specific to the repo. Examples:
     docs: "it lies". config: "it misroutes". API: "a released client breaks".
     data: "a row is lost, duplicated, or visible to the wrong tenant". -->
- Behavior that differs from the PR's stated intent or acceptance criteria.
- Data lost, duplicated, corrupted, or visible to the wrong user/tenant.
- A released client, public API, event name, or stored format breaks.
- A rollback becomes impossible (non-backward-compatible migration, one-way data change).
- Secrets, PII, or tokens end up in logs, errors, URLs, or client bundles.

## What Important means here
Reserve **Important** for findings that would break behavior, leak data,
corrupt state, or block a rollback, plus any violation of a rule in AGENTS.md /
CLAUDE.md / this file. Style, naming, and refactoring ideas are Nit at most.
For speculative code, security-sensitive surface added with no caller (crypto,
parsing untrusted input, auth, network) is **Important**; other needless
complexity is **Nit**, even when it violates the simplicity rules.
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
- flag code this PR adds that nothing in the PR or existing code calls or
  needs: stubs, placeholders, single-implementation abstractions without a
  required test/platform seam, unused parameters/options/config; safe path:
  the acceptance criteria use it, or a test or platform boundary needs the seam.
- New or changed API routes have an integration test and validate input.
- Queries are scoped to the caller's tenant/user.
- New behavior is behind a flag (default off) and the PR says how to revert.
- Changed function/endpoint/event contracts: every caller and consumer still works.

## Summary format
Start with a count: "2 important, 1 nit." When there is nothing to fix, say
"0 findings: nothing to fix." plainly. If part of the change could not be
reviewed, say so; an incomplete review is not a clean one.

<!-- Repo-specific rules go below. Format (from OpenAI's Codex guidance):
     flag <behavior>; safe path: <when it is fine>. Test each new rule with one
     change that should trigger it, one safe counterexample, and one unrelated change. -->
