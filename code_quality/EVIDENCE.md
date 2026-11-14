# Evidence audit

Every design decision in this kit, with the evidence behind it, how fresh that
evidence is, and what kind it is. Audited 2026-11-08. Bracketed numbers refer
to [REFERENCES.md](REFERENCES.md).

**Kinds of evidence, strongest first:**
- **Production:** a company describing what it runs on its own code, with outcome data.
- **Study:** a controlled experiment or paper. Usually benchmarks, often older models.
- **Experiment:** one team's internal trial, not company policy.
- **Guidance:** documentation or advice without outcome data.
- **Own design:** this kit's invention, with no outside evidence yet.

**Freshness:** "fresh" means published within the last 12 months (since Nov 2025).
"Stale" means it predates agent-written code at scale (before 2025).

## Decisions

| # | Decision | Evidence | Date | Kind | Fresh? | Verdict |
|---|---|---|---|---|---|---|
| 1 | Find, then verify each finding before it reaches anyone | Anthropic verifier votes [1]; Google Mandiant multiple validation agents + synthesis, three outcomes (confirmed / disproven / rejected) [74]; Anthropic Code Review GA [75] | Oct 2026, Aug 2026 | Production | ✅ | **Keep** |
| 2 | Risk tiers 0 / 1 / 2 humans; the change sets the tier | Anthropic [1] | Oct 2026 | Production | ✅ | **Keep**, but Google still has engineers approve all AI code [72], so T0 stays narrow and earned |
| 3 | Agent approvals never count; approval pinned to a commit; sensitive paths always human | Anthropic [1] | Oct 2026 | Production | ✅ | **Keep** |
| 4 | Precision over recall; drop style; cap nits | Anthropic [1]; OpenAI [10]; Google CTO office: "stop nitpicking style" on agent code [73] | Dec 2025 – Oct 2026 | Production + guidance | ✅ | **Keep** |
| 5 | Findings need `file:line` evidence and a failure scenario | Anthropic REVIEW.md docs [3]; OpenAI review rules [11] | 2026 | Guidance | ✅ | **Keep** |
| 6 | Both model families hunt for bugs | Song: one same-model + one cross-model review found 56.7% of planted errors vs 42.7% for two same-model reviews [77] | Oct 2026 | Study (planted errors) | ✅ | **Keep for Tier 2** (deep); off by default elsewhere since 2026-11-13, measured with `conditional_catches` |
| 7 | ~~The other family verifies each finding~~ | Was based on one practitioner blog [51] and Codex's own reasoning | Mar 2026 | Opinion | ✅ but weak | **Changed** (see below) |
| 7′ | One configurable family verifies in a fresh session (default Claude); deep mode uses both | Song: a fresh same-model session isn't significantly worse than a top-tier cross-model reviewer [77]. Xiang et al.: Claude reviewing Codex 71.6% → 89.7%; Codex reviewing Claude 91.4% → 82.8% [76] | Jul, Oct 2026 | Studies (older models, no test execution in [76]) | ✅ | **New default.** Claude chose it, so confirm with your evals |
| 8 | Verifiers may run tests and reproductions in a throwaway checkout | OpenAI: repo access + execution beats diff-only review [10]; Google Mandiant: human expert executes the proof of concept before a finding counts [74] | Dec 2025, Aug 2026 | Production | ✅ | **New** (the earlier verifiers only read code) |
| 9 | Strict machine gates, merge queue, flags, rollback | Anthropic [1]; Google CTO office "conditional LGTM": approval only takes effect once tests pass [73]; OpenAI harness used minimal gates [12] | Feb – Oct 2026 | Production vs experiment | ✅ | **Keep.** OpenAI's minimal gates were one team's experiment, not production policy |
| 10 | Evidence packet: one page for the human reviewer | Google CTO office: an AI "reviewer guide" on every PR (summary, breakage points, risk) [73] | Apr 2026 | Guidance | ✅ | **Keep** (it now has outside support) |
| 11 | Round cap 3, delta re-review, no repeats | Anthropic: mean rounds 2.08 → 1.32 [1] | Oct 2026 | Production | ✅ | **Keep** |
| 12 | Incidents become reviewer evals; keep a change only if the score improves | Anthropic [1]; OpenAI review-rule evals [11]; Google Mandiant grading agent on synthetic codebases [74] | Jul – Oct 2026 | Production | ✅ | **Keep** |
| 13 | ~~Target ≥ 90% actionable findings~~ | Google Tricorder < 10% effective false positives [27] | **2015** | Production, but for static analyzers | ❌ stale | **Changed** (see below) |
| 13′ | Target ≤ 5% of findings marked wrong and ≥ 50% leading to a code change | Anthropic < 1% marked incorrect [7]; OpenAI 52.7% led to a change [10]; AutoCommenter's 80% useful-ratio target [31] | Dec 2025 – Mar 2026 (and 2024) | Production | ✅ | **New** |
| 14 | Test-integrity detector (skipped tests, removed asserts, suppressions, gate-script edits) | OpenAI documents agents gaming checks [14]; no published fix | — | Own design | n/a | **Keep, in shadow mode first.** Measure how often it catches something real |
| 15 | Policy and scripts from the base branch; agents can't edit the quality system | Anthropic: REVIEW.md read from the base branch [3]; GitHub Actions hardening [40] | 2026 | Guidance | ✅ | **Keep** |
| 16 | Recurring cleanup and doc-gardening agents | OpenAI harness [12] | Feb 2026 | Experiment | ✅ but unproven | **Optional**, as before |
| 17 | Small PRs, ~400-line split | Google small CLs [25] | 2019 | Guidance (pre-agent) | ❌ stale | **Keep.** Anthropic's 2026 data supports it: most PRs over 1,000 lines get findings [1] |
| 18 | One family hunts and verifies by default; the second family joins on Tier 2 (`deep_families`) | Anthropic and OpenAI verify within one family in production [1][10]; a fresh same-model session verifies about as well as a cross-model one [77]; mixing families still finds more planted errors, 56.7% vs 42.7% [77], so that gain is bought where the tier warrants it | Dec 2025 – Oct 2026 | Production + study | ✅ | **New default** (2026-11-13). Widen with `families` only when `conditional_catches` shows codex-only catches |
| 19 | Solo profile: Tier 2 takes one approval, compensated by deep review with both families and execution verification | None. Google has engineers approve all AI code [72]; for one person that engineer is the director | — | Own design | n/a | **New** (2026-11-13). The sign-off is a forced packet read, not an independent check |
| 20 | Tier 0 may include additive code behind a default-off flag, judged by the classifier against a checklist, size-capped, sampled weekly | Anthropic's Low tier: "additive behind a flag" with 0 humans [1] | Oct 2026 | Production | ✅ | **New, opt-in** (`allow_model_t0`; on in the solo profile). Deterministic floors still win |

## What changed in the kit because of this audit

1. **Verification routing.** `[review].verifier` (default `"claude"`) replaces
   "the other family always verifies". Deep mode and Tier 2 still use both families.
2. **Verification can execute.** `[review].verify_with_execution = true` runs
   verifiers in a throwaway git worktree where they may run tests and write
   reproductions. It needs a sandboxed environment (CI runner or sandboxed session).
3. **Cross-model review must earn its place.** `[review].families` can be
   reduced to one family. `evals/run_evals.py` reports `conditional_catches`,
   meaning real bugs only one family found.
4. **Precision targets come from 2025–26 LLM reviewers**, not 2015 static analysis.
5. **Google's 2026 position is recorded.** Engineers still approve all AI code
   (75% of new code is AI-generated [72]). So the kit's T0 (no human) tier
   stays the exception: docs and copy, plus whatever your own data earns.

## What changed on 2026-11-13

1. **One family by default.** `[review].families = ["claude"]`;
   `[review].deep_families = ["claude", "codex"]` applies at deep depth, which CI
   uses on Tier 2. Decision 6 is kept for Tier 2 and measured, not assumed, elsewhere.
2. **Solo profile.** `profiles/solo.toml`: the same policy with Tier 2 = 1 approval
   and `allow_model_t0 = true`. A test keeps the two files in sync.
3. **Model-lowered Tier 0 is bounded.** It needs `allow_model_t0`, no floor hit, the
   classifier's flag checklist and the T0 size cap, and it is marked in `risk.json`
   and the packet so the weekly audit can sample it.
4. **Evals run before your first incident.** A case may carry a `fixture` (base and
   head file trees). The folder ships with seed cases: adversarial, rule and clean,
   plus two `incident` cases that reproduce bugs this kit once had (the
   implementer that never ran, the repair that was never committed). The synthetic
   ones prove the pipeline works; the incident ones are the first real recall
   measurement. Add your own.

## What is still weak

- **Cross-model numbers come from benchmarks with older models** (Opus 4.7,
  GPT-5.5, Gemini 2.5) and planted or competitive-programming errors, not
  production PRs. Your own eval cases are the real test.
- **No lab publishes revert or incident rates by review setup** except
  Anthropic's single figure (reverts down more than 40%, Jun–Sep 2026 [1]).
- **Google publishes no 2026 precision numbers** for agentic code review. Its
  2026 sources describe architecture [74] and practice [73], not outcomes.
- **Anything this kit invented** (the test-integrity detector, the director
  sign-off marker, the T0 allowlist) has no outside evidence. That's why
  shadow mode comes first.

## Not used (couldn't verify on a primary page)

- "Manual review time down ~60%" on Codex-pre-screened PRs: secondary
  coverage of OpenAI DevDay (Sep 2026) and a 2025 customer case study.
- DORA "PR review time +91%" and similar figures: these come from a
  third-party report quoted in coverage, not from DORA.
- "84% of PRs over 1,000 lines get findings, averaging 7.5 issues":
  attributed to Anthropic, but not on the pages checked.
