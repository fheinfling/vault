# Research: how Anthropic, OpenAI and Google keep code quality high

Compiled 2026-11-08. References: [REFERENCES.md](REFERENCES.md). Primary sources are preferred. Numbers are as published
by each company and are not independently verified. Method: Claude read the
primary sources directly. Two research subagents covered OpenAI and Google in
parallel. Codex CLI (`gpt-6-astra`, high reasoning, live web search) ran an
independent deep-research pass without seeing Claude's draft, and later
reviewed the finished kit (see "Pairing with Codex" below).

---

## Anthropic: verify findings, then allocate humans by risk

Source: [How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/)
plus the [Code Review docs](https://code.claude.com/docs/en/code-review),
[REVIEW.md customization](https://code.claude.com/docs/en/code-review-customize) and
[best practices](https://code.claude.com/docs/en/best-practices).

- **Context.** Claude writes about 90% of Claude Code. Merged PRs more than
  doubled from June to September, while the revert share fell by more than 40%.
  The explicit goal: *"the minimum amount of human review time that doesn't
  raise reverts or incidents."*
- **Pipeline.** Pre-PR self-checks (tests, build, lint, with output shown, plus
  a fresh-context subagent review) → required CI → AI review (parallel bug
  hunters, then **verifiers vote**, survivors ranked by severity) → separate
  security pass (open findings block) → **risk classifier** sets 0, 1, or 2
  human approvals → merge queue reruns on latest main → flag, then internal,
  then `latest`, then `stable` → author and on-call watch, flags off without a deploy.
- **Filtering.** Style, diagnostics, and cleanup-only findings are dropped,
  and the filters were A/B tested. Tags: **Important / Nit / Pre-existing**.
  Pre-existing keeps old bugs from looking like the author's fault. Every
  finding must be answered, and a later fixing commit clears it automatically.
- **Tiers.** Low (docs, copy, internal, additive behind a flag) gets 0 humans.
  Medium (ordinary; caching, auto-updater, system prompts) gets 1. High gets 2.
  *"The change sets the tier, not the author."* CI workflow files and
  owner-listed sensitive paths always need a person. *"An agent's approval
  never counts as a person's."* On an agent-written PR, the person directing
  the agent counts as one reviewer. Approvals cover one commit.
- **Results.** PRs with substantive review comments went from 16% to 54%.
  Pass after one round went from 65% to 78%. PRs needing 5+ rounds went from
  8.9% to 1.5%. Mean reviews to pass went from 2.08 to 1.32.
- **Improvement loop.** Incident PRs become an expert-checked eval set that
  runs daily. Prompt, tool, or model changes are kept only if the score
  improves. Noise is measured by author response (fixed / wrong / intentional
  / didn't matter) and by runs on known-clean PRs. Priority: *"catching real
  bugs first and cutting review rounds second."* Round cap of 3, then *step
  back*. Diff-only re-review later. Flag the bug class. No repeats.
- **Config.** CLAUDE.md stays short (stack, conventions, goals, non-goals).
  REVIEW.md says *what failure looks like in this repo*, caps nits, lists paths
  to skip, demands `file:line` evidence, and asks for a summary that starts
  with a count. It is read from the protected base branch, never from the PR.
- **Determinism.** *"If something has to happen every time, make it a hook, a
  deny rule, or a managed setting. CLAUDE.md is for guidelines."* A Stop hook
  can force a passing typecheck. *"A reviewer that's asked to find problems
  will always find something"*, so always say "skip style".

## OpenAI: precision first, give the reviewer context and execution, encode invariants

Sources: [A practical approach to verifying code at scale](https://alignment.openai.com/scaling-code-verification/),
[Custom code review rules for Codex](https://developers.openai.com/blog/custom-code-review-rules-for-codex),
[Harness engineering](https://openai.com/index/harness-engineering/),
[AGENTS.md guide](https://developers.openai.com/codex/guides/agents-md/),
[How OpenAI uses Codex](https://openai.com/business/guides-and-resources/how-openai-uses-codex/).

- **Precision over recall.** OpenAI *"accepted a measured tradeoff: modestly
  reduced recall in exchange for high signal quality and developer trust."*
  Findings are judged by expected value: the chance a finding is right,
  weighed against verification cost and false-alarm cost.
- **Repo access and execution beat diff-only review**, catching more critical
  issues with fewer false alarms. Dedicated training for review helped further.
- **Verification is cheap.** A verifier at a small fraction of the
  generator's tokens caught a large share of known high-severity issues.
- **Actionability.** Authors changed code after 52.7% of comments. Reviewer
  comments appeared on 36% of fully Codex-generated PRs, and 46% of those led
  to a change.
- **Separate the reviewer from the training grader.** Reward models may be
  over-sensitive. A deployed reviewer must not over-assert intent. They watch
  whether the generator learns to game its own checks, and warn that
  *"over reliance is a serious risk"*: the reviewer is one layer, not a guarantee.
- **Custom rules** live under `## Code Review Rules` in the nearest AGENTS.md.
  Rules gave 98% recovery of required findings versus 58.3% without them. Do
  not put mechanical checks (CI), volatile implementation details, or broad
  instructions in rules. Test each rule with **a trigger, a safe
  counterexample, and an unrelated change**, scoring coverage, restraint,
  retention, and actionability. Rules don't replace tests, branch protection,
  or approvals.
- **Harness engineering** (~1M lines, ~1,500 PRs, no hand-written code). A
  ~100-line AGENTS.md serves as a *map* into a `docs/` system of record.
  "Golden principles" are enforced mechanically (custom linters and structural
  tests). Recurring background agents scan for drift, update quality grades,
  and open small cleanup PRs (the team had been losing 20% of its week to "AI
  slop"). A doc-gardening agent fixes stale docs. Agents review agents.
  Minimal blocking merge gates, because corrections are cheap.

## Google: code health, ownership, and tools trusted because they're quiet

Sources: [eng-practices: standard](https://google.github.io/eng-practices/review/reviewer/standard.html),
[small CLs](https://google.github.io/eng-practices/review/developer/small-cls.html),
[SWE at Google ch. 9 (review), 11 (testing), 20 (static analysis), 22 (LSCs), 23 (CI)](https://abseil.io/resources/swe-book/html/ch09.html),
[Tricorder (ICSE '15)](https://research.google/pubs/archive/43322.pdf),
[Resolving review comments with ML](https://research.google/blog/resolving-code-review-comments-with-ml/),
[AutoCommenter](https://research.google/pubs/ai-assisted-assessment-of-coding-practices-in-industrial-code-review/),
[Resolving code review comments with machine learning (ICSE-SEIP 2024)](https://research.google/pubs/resolving-code-review-comments-with-machine-learning/).

- **Standard.** *Approve once the change definitely improves overall code
  health*, not once it is perfect. Facts beat preferences. Optional comments
  are prefixed **"Nit:"**. Don't accept "I'll clean it up later".
- **Small, self-contained CLs** with their tests. They are reviewed more
  thoroughly and roll back easily.
- **Three roles:** LGTM for correctness, **OWNERS** approval for the code
  area, and **readability** for language idiom. These are responsibilities,
  not necessarily three people.
- **Presubmits** run before a human looks. **TAP** runs affected tests after
  submit, finds the culprit, and rolls back fast. Flaky tests are actively
  managed (above ~1% flakiness, people stop trusting the suite). The
  **Beyoncé rule**: if you liked it, you should have put a test on it.
- **Tricorder.** A new check is admitted only with **<10% effective false
  positives** ("technically right but unhelpful" counts as false). Findings
  must be actionable, ideally with a fix. A "not useful" button disables noisy
  analyzers. Only *new* issues in changed code are shown.
- **Large-scale changes.** Rosie shards a change by owner. A **global
  approver** plus pattern tooling auto-approves conforming shards and humans
  look only at anomalies. *Human effort should scale sublinearly.* This is the
  model for T0.
- **ML in review.** Google's comment-resolution model addresses 52% of
  comments at a 50% precision target offline (blog, May 2023). In deployment,
  authors resolve **7.5% of all reviewer comments** by applying an ML-suggested
  edit (Frömmgen et al., ICSE-SEIP 2024). AutoCommenter's *useful ratio*
  (thumbs-up or "please fix" clicks) plateaued at 54%. After a rater study in
  April 2023, Google suppressed **22 non-actionable best-practice links**, which
  brought it to the **80% target** for wider rollout (Vijayvergiya et al.,
  AIware, July 2024). It gave up coverage to gain precision.

---

## Where they agree (adopted as-is)

| Principle | Anthropic | OpenAI | Google | In this kit |
|---|---|---|---|---|
| Machines enforce what must never bend | hooks, deny rules, required CI | "tests, branch protection, approvals are the hard enforcement" | presubmits, TAP | Stop hook, PreToolUse hook, `quality-gate` required check |
| Precision > recall at the human interface | verifiers vote, style dropped | expected-value filter, P0/P1 | Tricorder <10% FP, AutoCommenter suppression | verifier pass, nit cap, no style; ≤ 5% marked wrong, ≥ 50% lead to a change |
| Evidence, not claims | show test output; `file:line` | cite logs, exit codes | actionable findings with fixes | schema requires scenario + `file:line` |
| Small, reversible changes | small PRs, flags, revert path | cheap corrections | small CLs, rollback | PR template "how to revert", 400-line split rule |
| Recurring issues fixed at the source | skill / lint / instruction file | golden principles → linters | analyzers with fixes | "3× → lint rule" rule, eval `rule` cases |
| Measure the reviewer | daily incident evals, noise labels | human eval, recall on known bugs | "not useful" rates, acceptance | `evals/` runner, outcome labels |

## Where they differ, and the call made here

| Tension | Positions | Decision |
|---|---|---|
| Human on every change? | Google: yes (peer + owner). OpenAI harness: agent-only review. Anthropic: risk-tiered | **Risk-tiered**, with T0 earned per category from shadow-mode data (Codex's point: start narrow) |
| Bugs vs maintainability | Anthropic/OpenAI review for bugs. Google also reviews design and readability | Block on correctness and *stated* invariants only. Maintainability is handled by T2 for new patterns, golden-principle linters, and cleanup agents, not by nits |
| Minimal vs strict merge gates | OpenAI harness: minimal (corrections are cheap). Google/Anthropic: strict presubmit + merge queue | **Strict deterministic gates, light human gates.** CI time is cheap and human time isn't |
| Voting vs evidence | Anthropic: verifier votes | **Evidence beats votes.** A confirmation with `file:line` keeps a finding, and a refutation needs counter-evidence |
| Diff-only re-review | Anthropic: diff-only after a few passes | Delta review **plus re-verification of every still-open finding**, with repo-wide read access kept |

---

## Pairing with Codex (`gpt-6-astra`)

Codex ran its own research blind. It then reviewed the finished kit. Its probes exposed
seven fail-open bugs, all fixed (see the review file linked below). What its
research changed in the design:

- **Don't multiply miss rates.** Two reviewers that each miss 10% do not
  jointly miss 1%, because their errors correlate. Measure conditional catches
  ("what did Claude catch that Codex missed?"). `evals/` reports them. The
  2026 studies in [EVIDENCE.md](EVIDENCE.md) later narrowed how cross-model
  review is used.
- **Incomplete review is not a clean review.** `complete=false` blocks. The
  hosted Claude Code Review check never fails on its own, so a missing verdict
  must not count as zero bugs.
- **Policy from the protected base revision.** The controller (CI) loads the
  trusted policy and scripts. A Markdown line saying "follow the rules"
  protects nothing.
- **"Pre-existing" is provenance, not severity.** An old bug the change newly
  exposes blocks. This is encoded in `bug-hunter.md` and `verifier.md`.
- **Unresolved ≠ refuted.** Failing to reproduce a bug doesn't make it false.
  Unresolved Important findings stay blocking, but the *agent* settles them by
  writing a test, not a human.
- **Risk laundering.** Flag flips and config changes need tiering too, or the
  risk just moves out of the PR.
- **Start in shadow mode** and grant T0 category by category.

Codex's full independent report is in
[research/codex-independent-research.md](research/codex-independent-research.md).
Its review of this kit, and what was changed in response, is in
[research/codex-review-of-kit.md](research/codex-review-of-kit.md).
