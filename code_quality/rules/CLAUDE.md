@AGENTS.md

<!-- Keep this file short: only what Claude can't work out from the code.
     Replace the placeholders, delete what doesn't apply. -->

## Verify your work
- Fast checks: `code_quality/scripts/quality_gate.sh fast` (also runs automatically when you finish a turn)
- Full gate: `code_quality/scripts/quality_gate.sh full`
- Single test: <command to run one test file>
- Review loop before PR: `code_quality/scripts/review_loop.sh --intent "<task>"`

## Where the code misleads
- <old pattern that is everywhere but new code must not copy>
- <directory that looks unused but isn't>

## Decisions not visible in the code
- <which error helper / logger / validation library the team settled on>
- <how deploys and flags work>
