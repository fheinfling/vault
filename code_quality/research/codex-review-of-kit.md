# Codex (gpt-6-astra) review of the kit, and what changed

Codex reviewed a read-only copy of the finished kit with
`codex exec -m gpt-6-astra` (high reasoning). The run stopped partway, so it never wrote a
final ranked report. Its interim status message and the probe scripts it ran
against the code are in the log, and every issue below was reproduced from
those probes and then fixed. Live end-to-end runs (both families) happened
before it stopped. To re-run this review:

```bash
cp -r code_quality /tmp/kitcopy && cd /tmp/kitcopy
codex exec -m gpt-6-astra --skip-git-repo-check -s read-only \
  "Read-only review of ./code_quality (an agent code-quality kit). Find fail-open paths, \
   trust-boundary holes, script bugs, and places human time is wasted or removed unsafely. \
   Rank findings MUST-FIX / SHOULD-FIX / IDEA with file:line and a failure scenario."
```

> Interim message from Codex: *"I've found several potential false-green paths:
> persisted state can suppress new findings, and the approval gate can count a
> PR author without an approval on the reviewed commit. I'm checking the
> workflow's trust boundary and event handling next…"*

| # | Codex finding (from its probes) | Severity | Fix |
|---|---|---|---|
| 1 | The ledger suppressed any new finding with the same `(file, bug_class)` as *any* earlier finding, even a fixed one. A reintroduced bug, or a second bug of the same class, went unreported | MUST-FIX | Only *open* findings within ±3 lines suppress repeats (`dedupe()` in `cross_review.py`). Closed findings never do. Open ones are re-verified each round |
| 2 | `approval_gate.py` counted the PR author as "director" with no action at all on the reviewed commit. For T1, that's zero humans | MUST-FIX | The director counts only via an explicit review on the head SHA containing `/director-approve`. The hook blocks agents from posting it. Bot identity recommended |
| 3 | `decide()`: an evidence-free "refuted" plus "unresolved" dropped a security finding | MUST-FIX | A refutation counts only if it cites counter-evidence |
| 4 | `decide()`: a verifier escalating a nit to Important (unresolved) was ignored | MUST-FIX | Unresolved findings take the worst severity anyone assigned |
| 5 | `dedupe()` merged two *distinct* security bugs 2 lines apart because they shared a category | MUST-FIX | Merge only when the bug class matches, or when the line *and* category match exactly |
| 6 | T0 matched directories: `docs/server.py` and `src/i18n/loader.ts` were T0, and so was `prompts/system.md` | MUST-FIX | T0 matches file types only. New T1 floor for prompts, skills, LLM config |
| 7 | Removing a coverage threshold (a `-` line in pytest.ini) wasn't detected; only added lines were scanned | MUST-FIX | Removed threshold, discovery, and gate-script lines in config files are flagged |
| 8 | (Codex was researching GitHub Actions hardening when the run stopped) | — | Claude followed the thread: on `pull_request`, GitHub runs the PR's own workflow file, so a PR could neuter the gate. Fixed with CODEOWNERS + code-owner review, and org required workflows. A related hole: base-branch gate commands still call PR-controlled `package.json` scripts. The integrity detector now flags redefined gate scripts |

Found by Claude during live testing (before the Codex review):

- Codex's own sandbox can't start inside Claude Code's sandbox. The review
  correctly reported **incomplete → blocking** (fail-closed worked). Added
  `QUALITY_CODEX_SANDBOX=outer` for already-confined environments, a
  `complete` flag for verifiers, and exit code 2 so the loop stops instead of
  "repairing" a tooling failure.
- The test-integrity detector and a real model finding reported the same
  `noqa` line twice. Dedupe now merges a same-line integrity signal.
- A removed SQL comment (`-- x` → diff line `--- x`) could be misread as a
  file header. Header parsing now tracks state.
- `mapfile` isn't available in macOS bash 3.2. Replaced with a portable loop.

## Second opinion: independent fresh-context review (Claude code-reviewer subagent)

Because Codex couldn't finish, a separate Claude reviewer that had not seen the
build conversation reviewed the scripts, hooks, and workflow. It confirmed the
schemas are OpenAI strict-mode compatible and the shell scripts are bash 3.2
safe. All of its findings were fixed:

| # | Finding | Fix |
|---|---|---|
| 1 | `approval_gate.py` crashed on reviews from deleted GitHub accounts (`user: null`) | Safe access; null users never count |
| 2 | CI `git archive … REVIEW.md` failed every run when the repo had no root REVIEW.md | REVIEW.md archived separately and optional |
| 3 | `classify_risk.py` called `claude -p` without `--permission-mode dontAsk`, which could hang CI | Added |
| 4 | Model-T0 lowering depended on parsing English reason strings | `deterministic()` returns a structured `floor_hit`; floors can never be lowered |
| 5 | `/director-approve` matched as a substring (quoted mentions counted) | Must be on its own line |
| 6 | The cached ledger could suppress a *new* finding as a "repeat" (cache poisoning) | The ledger never suppresses candidates. New findings and prior re-checks are merged by dedupe instead |
| 7 | `risk.json` had no SHA, so a stale tier could be trusted on approval events | `risk.json` records `sha`/`base_sha`/policy; the gate requires `sha == head` |
| 8 | Author responses were concatenated into the verifier prompt (prompt injection) | Separate `author_response_untrusted` field; verifier told to treat it as data |
| — | Codex temp output files leaked | Cleaned up in `finally` |
