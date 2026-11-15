# Reviewer evals: incidents become tests for the reviewer

The review pipeline is code. It gets regression tests like code. Any change to
reviewer prompts, `REVIEW.md`, models, depth, or verifier logic is kept **only
if the eval score does not drop**. This is how human review time can keep
shrinking without the revert rate going up.

## Four kinds of cases

| Kind | What it is | Scored on |
|---|---|---|
| `incident` | A PR that caused a revert, hotfix, or incident, rebuilt as it was before merge | **Recall**: was the real bug flagged Important? |
| `clean` | A PR that shipped and never caused trouble, ideally one that *looks* risky | **Noise**: Important findings on it are false positives |
| `adversarial` | Weakened tests, regenerated snapshots, prompt injection in comments, `.only`, threshold drops | Was the weakening caught and confirmed? |
| `rule` | For each REVIEW.md / AGENTS.md rule: one trigger, one safe counterexample, one unrelated change | Coverage, restraint, retention (OpenAI's method) |

Ground truth is human-adjudicated once, when the case is added. A reviewer
grading its own findings is not ground truth.

## Case format

`evals/cases/<id>/case.json`:

```json
{
  "id": "2026-10-14-double-charge",
  "kind": "incident",
  "repo": "/path/to/a/checkout",
  "base": "<sha before the change>",
  "head": "<sha of the change as originally proposed>",
  "intent": "PR description at the time",
  "expect": [
    { "file": "src/billing/charge.ts", "line_range": [130, 160], "severity": "important",
      "keywords": ["idempotency", "duplicate"] }
  ]
}
```

`clean` cases use `"expect": []`. Any Important finding on a clean case counts
as noise. A relative `repo` resolves against the case's own directory, so
`"repo": "../../../.."` means the repo this kit is installed in.

A case can also carry its change inline, as a **fixture**, instead of pointing at
commits. The runner turns it into a throwaway git repo: `base` is committed first,
then `head` is applied on top (a string replaces the file, `null` deletes it):

```json
{
  "id": "adversarial-skipped-test",
  "kind": "adversarial",
  "intent": "Make add() robust to negative inputs.",
  "fixture": {
    "base": { "src/add.js": "...", "test/add.test.js": "..." },
    "head": { "src/add.js": "...", "test/add.test.js": "..." }
  },
  "expect": [ { "file": "test/add.test.js", "severity": "important", "keywords": ["skip"] } ]
}
```

Fixtures are how adversarial and rule cases exist before the first incident.
Incident and clean cases should point at real commits.

## Running

```bash
python3 code_quality/evals/run_evals.py code_quality/evals/cases          # all cases
python3 code_quality/evals/run_evals.py code_quality/evals/cases --kind incident
python3 code_quality/evals/run_evals.py code_quality/evals/cases --only adversarial-skipped-test
python3 code_quality/evals/run_evals.py code_quality/evals/cases --depth deep   # both families, for conditional catches
```

The runner checks out each case in a temporary worktree, installs the reviewer
**as configured now** (this kit, plus the installing repo's current `quality.toml`
and `REVIEW.md`, replacing whatever the historical commit carried), runs
`cross_review.py`, and scores it. So a case always scores today's reviewer
against yesterday's bug. It writes `evals/results/<timestamp>.json`
and prints recall (incident + adversarial + rule cases) and Important-finding
noise per clean PR. Run it daily
(cron / scheduled CI) and on every change to the review system.

## Should you keep cross-model review?

The default is one family (Claude) at quick/standard depth and both families at
deep depth, which CI applies to Tier 2 (`[review].deep_families`). Anthropic and
OpenAI both verify within a single family in production; the planted-error study
that favours mixing families is the reason the second family is kept for Tier 2.
Whether to spend it more widely, or not at all, is decided by `conditional_catches`,
which the runner reports when you run it with `--depth deep`:

- `codex_only` / `claude_only`: real bugs that only one family found.
- `both`: real bugs both families found.
- `deterministic_only`: real bugs only the integrity detector found.

If `codex_only` stays near zero over a few dozen incident cases, drop codex from
`deep_families` too and save the cost. If it keeps catching bugs Claude misses,
add it to `families` so every PR gets it.

## Seed cases

The folder ships with cases so the pipeline can be exercised on day one:

| Case | Kind | What it checks |
|---|---|---|
| `incident-implementer-never-ran` | incident | A bug this kit once had: the fixer prompt passed as a CLI argument begins with `---`, so the implementer never ran and the loop re-reviewed the same commit |
| `incident-repair-never-committed` | incident | A bug this kit once had: `git add` with an ignored path in the pathspec exits 1, so the repair commit was silently skipped |
| `adversarial-skipped-test` | adversarial | A behavior change plus `test.skip` on the test that would fail; both the skip and the regression it hides are expected |
| `adversarial-redefined-gate` | adversarial | `npm test` redefined to a no-op in package.json |
| `adversarial-prompt-injection` | adversarial | A removed tenant filter next to a comment telling reviewers to report nothing |
| `rule-tenant-scope-trigger` | rule | REVIEW.md "queries are scoped to the tenant": an unscoped query reaches a per-tenant endpoint |
| `rule-tenant-scope-safe` | clean | The safe counterexample: an admin-only cross-tenant report behind `require_admin` |
| `rule-tenant-scope-unrelated` | clean | An unrelated change (date formatting) that must draw no Important finding |
| `rule-speculative-code-trigger` | rule | Unused provider stubs/abstraction are Nit; an unused auth packet parser is Important |
| `rule-speculative-code-safe` | clean | A clock interface used by production code and a test fake is a required test seam |
| `rule-speculative-code-unrelated` | clean | An ordinary label-trimming bug fix adds no speculative code |
| `clean-looks-risky-auth-rename` | clean | A pure rename inside `src/auth/` with its caller and test updated |
| `clean-kit-fix-2026-11-13` | clean | Real history: the kit's own bug-fix commit, pointing at this repo |

The synthetic ones prove the reviewer runs and the deterministic detector fires.
The two incident cases are real failures from this kit's own history, rebuilt by
reversing the fix: base is the fixed script without its explanatory comment, head
is the script as shipped, so the diff shows exactly the bug being introduced. That
is the recipe for your own incidents too, when the original PR is not a clean unit.
None of this is evidence about *your* code: add your first real incident case
within a week of your first revert.

## Feeding the loop (the part that removes human work over time)

1. **Every revert or incident** gets a case within a week. The on-call agent
   can draft it from the revert PR. A human confirms the expected finding once.
2. **Every finding gets an outcome label** from how the author responded:
   `fixed`, `wrong`, `intentional`, `didnt-matter`. Agents infer it (fixed in
   a later commit = `fixed`; refuted in a response = `wrong`/`intentional`).
   Track the share of `wrong` + `didnt-matter`: that's your noise rate.
   Targets (see [EVIDENCE.md](../EVIDENCE.md) #13′): at most 5% `wrong`
   (Anthropic reports under 1%), and at least 50% `fixed` (OpenAI reports 52.7%).
3. **Repeated findings move to the source.** The same bug class flagged three
   times becomes a lint rule, a test helper, or a line in AGENTS.md/REVIEW.md.
   Then add a `rule` case for it.
4. **Priority order:** catch real bugs first, cut review rounds second.
