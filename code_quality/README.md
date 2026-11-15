# code_quality: agent rules and flow for high quality with minimal human time

A drop-in kit for repos where Claude Code and/or Codex write most of the code.
It is built from how Anthropic, OpenAI and Google keep quality high
([RESEARCH.md](RESEARCH.md)). Claude reviews by default; Codex `gpt-6-astra`
joins on Tier 2, where a second model family is worth its cost.

**In one sentence:** agents implement, test, get reviewed by fresh-context
reviewers that verify their own findings (a second model family on Tier 2), and
repair until a deterministic gate is green. A risk tier then decides whether 0,
1, or 2 humans need to read a one-page evidence packet before the merge queue
and a flagged rollout. One person? Use `profiles/solo.toml`.

Read [FLOW.md](FLOW.md) first. It is the whole system on one page. [EVIDENCE.md](EVIDENCE.md) shows what each decision rests on and how current that is.

**Visual comparison:** open [docs/index.html](docs/index.html) in a browser to compare how Anthropic, OpenAI, Google, independent practitioners and this kit approach agentic code review. It has stage-by-stage swimlanes, a 13-dimension matrix with toggles, the points where they disagree, and their published numbers.

**For agents:** the same flow is a graph in [flow.graph.json](flow.graph.json)
(nodes, gates, commands, pass/fail edges, tiers, invariants). Query it instead
of reading the prose:

```bash
python3 code_quality/scripts/flow_graph.py path                 # task -> … -> done
python3 code_quality/scripts/flow_graph.py show verify          # what this step does, its command, its edges
python3 code_quality/scripts/flow_graph.py next verify blockers # -> repair
python3 code_quality/scripts/flow_graph.py list --json          # whole graph, compact
python3 code_quality/scripts/flow_graph.py validate             # edges + file refs consistent
```

## What's here

```
code_quality/
├── README.md                 ← you are here: install + daily use
├── INSTALL.md                ← the install, step by step, written for an agent to follow
├── install.py                ← one-command install / update of the mechanical parts (idempotent)
├── FLOW.md                   ← the pipeline, risk tiers, human touchpoints, metrics, rollout
├── RESEARCH.md               ← Anthropic / OpenAI / Google findings, decisions, Codex pairing
├── docs/index.html           ← visual side-by-side comparison of the approaches
├── EVIDENCE.md               ← every design decision vs. how fresh and strong its evidence is
├── REFERENCES.md             ← numbered reference list of every source used
├── flow.graph.json           ← the flow as a machine-readable graph (query with scripts/flow_graph.py)
├── quality.toml              ← THE policy: gate commands, models, tiers, path floors
├── profiles/solo.toml        ← the same policy for one human: Tier 2 = 1 approval + deep review
├── rules/
│   ├── AGENTS.md             ← implementer rules (Claude + Codex) → copy to repo root
│   ├── CLAUDE.md             ← `@AGENTS.md` + verify commands → copy to repo root
│   └── REVIEW.md             ← reviewer rubric: what Important means, evidence, caps → repo root
├── agents/                   ← prompts; also valid Claude Code subagents (.claude/agents/)
│   ├── bug-hunter.md         ← fresh-context, read-only finder
│   ├── verifier.md           ← skeptical verifier (fresh session, may run tests)
│   ├── risk-classifier.md    ← raise-only tier classifier
│   └── fixer.md              ← implementer instructions for repair rounds
├── schemas/                  ← JSON schemas for findings, verdicts, risk (structured output)
├── scripts/
│   ├── quality_gate.sh       ← deterministic gate (fast = Stop hook, full = CI)
│   ├── check_test_integrity.py ← catches deleted/skipped tests, removed asserts, suppressions…
│   ├── cross_review.py       ← find (Claude ∥ Codex) → cross-verify → rank → report
│   ├── review_loop.sh        ← gate → review → repair, max 3 rounds, then escalate
│   ├── classify_risk.py      ← tier = max(path floor, model opinion)
│   ├── evidence_packet.py    ← the one page a human reads
│   └── approval_gate.py      ← CI: enough HUMAN approvals on THIS commit for the tier?
├── hooks/
│   ├── settings.json         ← Stop hook + PreToolUse guard + deny rules → .claude/settings.json
│   └── protect_paths.py      ← agents can't edit the rules they're judged by
├── ci/quality-gate.yml       ← GitHub Actions: checks, review, approval gate, merge queue
├── ci/quality-gate-review.yml  ← review submitted/dismissed: permission-less relay
├── ci/quality-gate-refresh.yml ← workflow_run on the relay: re-run the PR run's gate job
├── templates/                ← PR template, CODEOWNERS (closes the workflow self-edit hole)
├── evals/                    ← incidents → reviewer regression tests; runner
├── tests/                    ← the kit's own regression tests (stdlib unittest)
└── research/                 ← Codex's independent research + its review of this kit
```

## Install into a repo

One command does the mechanical part, and re-running it later updates the kit:

```bash
python3 /path/to/code_quality/install.py <repo> --profile solo   # or --profile team
```

It copies the kit, creates the policy and rules files only where they are missing
(yours are never overwritten), installs the subagents and both workflows, merges the
hooks into `.claude/settings.json`, and prints what is still yours to do. To have an
agent do that part, send it [INSTALL.md](INSTALL.md); it says how, including the
`QUALITY_ALLOW_POLICY_EDITS=1` the install session needs.
`.github/workflows/quality-gate.yml`, `quality-gate-review.yml` and `quality-gate-refresh.yml`
are kit-owned and overwritten on updates.

Then:

1. **Fill in `quality.toml`.** Set `[checks].fast` (lint + types, under a
   minute) and `[checks].full` (everything CI runs), the path floors for your
   sensitive areas, and `[risk].humans` (GitHub logins whose approvals count).
2. **Make REVIEW.md specific.** Write 3–6 lines on *what failure looks like in
   this repo*. Delete rules that don't apply.
3. **Make CLAUDE.md specific.** Add the single-test command, misleading
   patterns, and decisions not in the code. Keep it short.
4. **GitHub settings.**
   - Ruleset on `main`: require PRs, require the `checks` and `quality-gate`
     status checks (source: GitHub Actions), dismiss stale approvals on push, require the merge queue,
     **require review from Code Owners**, and no bypass for bots or apps.
   - Copy `templates/CODEOWNERS` to `.github/CODEOWNERS`. On `pull_request`,
     GitHub runs the workflow *from the PR*, so without code-owner review a PR
     could rewrite the gate itself. On org plans, also add "Require workflows
     to pass" pinned to the default branch.
   - Best: have agents push and open PRs as a **bot identity** (a GitHub App or
     machine user), so the human who directed them can approve normally. In
     solo setups where the agent uses your account, sign off with a PR review
     whose body contains `/director-approve` on the head commit. The hook
     blocks agents from doing this themselves.
   - With `[review].where = "ci"`: add the `ANTHROPIC_API_KEY` secret, and
     `OPENAI_API_KEY` while `[review].deep_families` includes codex (Tier 2 reviews).
     With `"local"` (see below): no secrets at all.
   - Create the labels `agent-authored`, `risk:t0`, `risk:t1`, `risk:t2`.
   - A PR that installs or upgrades the kit changes the gate itself, so the gate can't
     judge it: `quality-gate` fails on such PRs **on purpose**. Read the local evidence
     and merge them deliberately: give repository admins bypass permission in the
     ruleset. Every other PR runs entirely under the base branch's policy and scripts.
   - `[ci].runs_on` and `[ci].setup` pick the runner and toolchain for the `checks`
     job (a Swift repo: `runs_on = "macos-15"`).
5. **Start in shadow mode** (FLOW.md, "Earning autonomy"). Keep reviewing as
   you do today for two weeks, then turn on tier-based approvals.

Requirements: `git`, Python ≥ 3.11 (stdlib only), `claude` CLI, `codex` CLI, `gh`.

Kit self-tests, to run after editing anything under `code_quality/`:

```bash
python3 -m unittest discover -s code_quality/tests
```

## Daily use

The agent does all of this itself because AGENTS.md tells it to. You don't
run these by hand.

```bash
code_quality/scripts/quality_gate.sh full                      # deterministic gate
code_quality/scripts/review_loop.sh --intent "<task + acceptance criteria>"
#   → exit 0: converged, .quality/packet.md is ready, open the PR
#   → exit 3: hit the round cap, .quality/packet.md explains what's stuck
code_quality/scripts/classify_risk.py --model                  # what tier is this?
```

Inside Claude Code, the subagents can also be used directly, for example
"have the bug-hunter review my diff against the spec". In a Codex-led repo,
set `implementer = "codex"` in `quality.toml`. By default one family (Claude)
hunts and verifies; `[review].deep_families` adds Codex at deep depth, which CI
uses on Tier 2. Put both in `[review].families` to cross-review every PR, and
`[review].verifier` picks who verifies.

**Local review mode, no API keys.** Set `[review].where = "local"` when the
reviewers should run on a developer's machine through the logged-in `claude` and
`codex` CLIs instead of in CI. The loop runs as above; after pushing the head
commit, `python3 code_quality/scripts/evidence_comment.py publish --pr <n>` posts
the packet plus the machine-readable evidence to the PR. CI then runs only the
deterministic `checks`, and `quality-gate` takes the evidence from that comment
(`publish --approve` also submits your sign-off, so it is one command: an
approval on a PR someone else opened, the director review on your own),
accepting it only from a human in `[risk].humans` and only for the head commit.
No comment, no merge. What you give up: the review no longer runs in a neutral
environment, so the sign-off rests on the publisher's machine and judgment.

**Gate re-evaluation.** Pushes start `quality-gate.yml` through `pull_request`;
the workflow also runs on `merge_group`. A review submitted or dismissed runs `quality-gate-review.yml`, a relay with no permissions (review events run the PR's copy of a workflow, read-only on fork PRs) that only names the PR and head commit in its run title. That completion triggers `quality-gate-refresh.yml` through `workflow_run`, which always runs the default branch's copy with the `refresh` job granted `contents: read`, `actions: write` and `pull-requests: read` (for the pulls API), fork PRs included, using only `gh` and `jq` with no checkout or repository code. It accepts the title only in the exact expected form and only for a PR whose live head is that commit, selects that PR's newest `pull_request` run for the head (GitHub's non-empty `pull_requests` must contain the PR; fork runs with an empty list need the exact `quality-gate for PR #N` title plus the PR's head repository and branch), polls every 15 seconds for up to 20 minutes until it completes, then re-runs only its `quality-gate` job, whatever its verdict. It skips the re-run with a notice when the run was cancelled or the head moved meanwhile: re-running a stale run would cancel the new head's run through the shared concurrency group, and the new run reads the review itself. A re-run keeps the original event, so the gate job re-reads the PR's live base and stages the policy from it; if `[review].where` changed, it fails closed and asks for a full re-run.
`publish` does the same after posting evidence; `publish --approve` leaves the
refresh to the review event. If no PR run exists yet, the refresh reports a notice;
that run will read the review/evidence when it runs. A wait timeout fails explicitly.
Fork runs predating the PR-specific title are skipped with a notice. If GitHub refuses
a re-run (for example, after 30 days), refresh fails and names the run; push a commit
or close and reopen the PR to start a fresh gate run.
Reviews refresh the existing PR gate check instead of adding another gate run.
The ruleset still requires `checks` and `quality-gate`; the relay and refresh jobs are not required.

**Nested sandboxes.** Codex's own sandbox can't start inside another sandbox
(for example, when the loop runs from a sandboxed Claude Code session). The
review then reports *incomplete* and blocks, which is deliberate: it fails
closed. In that case set `QUALITY_CODEX_SANDBOX=outer`, but only where the
outer environment already confines the process.

## Recurring agents (optional, from OpenAI's harness engineering)

These keep the codebase from slowly rotting without any human time. Schedule
them with `/schedule` or `/loop` in Claude Code, or as cron CI jobs. Each one
opens small PRs that go through the same flow (mostly T0/T1):

- **Daily reviewer evals:** `python3 code_quality/evals/run_evals.py code_quality/evals/cases`
  (ships with seed cases, see `evals/README.md`; add your own incidents). Seed cases
  that review the kit's own history stay in the kit's source repository; the installer leaves
  them out, because their commits exist nowhere else.
- **Weekly drift sweep:** "Find code that violates AGENTS.md / REVIEW.md rules
  or duplicates an existing helper; open one small PR per pattern."
- **Weekly doc gardener:** "Find docs and CLAUDE.md lines that the code
  contradicts; open fix-up PRs."
- **Repeated-finding promoter:** "From `.quality` ledgers of merged PRs, find
  bug classes flagged 3+ times and propose a lint rule or AGENTS.md line."

## Limits (honest)

- AI review lowers risk; it doesn't remove it. *"A review with no findings
  doesn't mean your code is error-free."* That's why there are flags, a merge
  queue, canaries, and evals.
- Two model families still share blind spots. Their miss rates don't simply
  multiply, so the eval set measures the whole pipeline.
- The path floors are only as good as your list of sensitive paths. Review the
  classifier's misses weekly at first.
- `approval_gate.py` and the workflows are templates. Test them on a sandbox
  repo before making them required.

## References

Based on published work by Anthropic, OpenAI and Google, plus GitHub's docs.
Full numbered reference list: [REFERENCES.md](REFERENCES.md).
