#!/usr/bin/env python3
"""Independent review: find in parallel, verify in a fresh session, post only survivors.

  find    Each configured model family ([review].families; [review].deep_families at
          deep depth, which CI uses on Tier 2) reviews the change independently
          (read-only, fresh context, no access to the implementer's conversation).
          Deterministic test-integrity signals are added as candidates.
  verify  Every candidate is checked by the configured verifier family in a fresh
          session ("other" = the family that did not raise it; deep: every family).
          Verifiers try to DISPROVE the finding with file:line evidence. Votes rank
          work; evidence decides.
  rank    Dedupe, drop refuted, cap nits, drop repeats of findings already
          posted on earlier rounds, sort by severity.

Exit codes: 0 = no blockers, 1 = blockers (confirmed or unresolved Important),
2 = review incomplete (a reviewer/verifier failed). 1 and 2 both block merging. Writes .quality/review-<sha>.json and
.quality/review.md (PR-comment-ready summary).

Usage: cross_review.py [--base origin/main] [--since <sha>] [--depth quick|standard|deep]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import tempfile
from pathlib import Path

import qlib

READ_ONLY_TOOLS = "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(git blame:*),Bash(rg:*),Bash(ls:*)"
SEV_RANK = {"important": 0, "pre_existing": 1, "nit": 2}


def prompt_body(name: str) -> str:
    text = (qlib.KIT / "agents" / name).read_text()
    return text.split("---", 2)[-1].strip() if text.startswith("---") else text


def review_md() -> str:
    p = qlib.review_rules_path()
    return p.read_text() if p else ""


def run_claude(prompt: str, schema_file: Path, model: str, cwd: Path | None = None, execute: bool = False) -> dict:
    tools = READ_ONLY_TOOLS + (",Bash,Write,Edit" if execute else "")
    res = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--output-format", "json",
         "--json-schema", schema_file.read_text(), "--allowedTools", tools,
         "--permission-mode", "dontAsk"],
        capture_output=True, text=True, cwd=cwd or qlib.repo_root(),
    )
    try:
        out = json.loads(res.stdout)
        if out.get("is_error") or out.get("structured_output") is None:
            raise ValueError(out.get("result") or out.get("subtype"))
        return out["structured_output"]
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        raise RuntimeError(f"claude failed: {e}\n{res.stderr[-1500:]}") from e


def codex_sandbox_args(execute: bool = False) -> list[str]:
    """Codex's own OS sandbox can't start inside another one (e.g. Claude Code's
    sandbox, some containers). Set QUALITY_CODEX_SANDBOX=outer ONLY when the outer
    environment already confines the process (ephemeral CI runner, sandboxed
    agent session); the reviewer prompt is still read-only by instruction."""
    if os.environ.get("QUALITY_CODEX_SANDBOX") == "outer":
        return ["--dangerously-bypass-approvals-and-sandbox"]
    return ["-s", "workspace-write" if execute else "read-only"]


def run_codex(prompt: str, schema_file: Path, model: str, cwd: Path | None = None, execute: bool = False) -> dict:
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tmp:
        out_path = tmp.name
    res = subprocess.run(
        ["codex", "exec", "-m", model, *codex_sandbox_args(execute), "--ephemeral", "--skip-git-repo-check",
         "--output-schema", str(schema_file), "-o", out_path, "-"],
        input=prompt, capture_output=True, text=True, cwd=cwd or qlib.repo_root(),
    )
    try:
        return json.loads(Path(out_path).read_text())
    except (json.JSONDecodeError, FileNotFoundError) as e:
        raise RuntimeError(f"codex failed ({res.returncode}): {e}\n{res.stderr[-1500:]}") from e
    finally:
        Path(out_path).unlink(missing_ok=True)


def run(family: str, prompt: str, schema: str, cfg: dict, cwd: Path | None = None, execute: bool = False) -> dict:
    schema_file = qlib.KIT / "schemas" / schema
    if family == "claude":
        return run_claude(prompt, schema_file, cfg["agents"]["claude_model"], cwd, execute)
    return run_codex(prompt, schema_file, cfg["agents"]["codex_model"], cwd, execute)


class ScratchWorktree:
    """Throwaway checkout of HEAD where verifiers may run tests and write
    reproductions. Discarded afterwards, so nothing a verifier does reaches the PR."""

    def __enter__(self) -> Path:
        self.path = Path(tempfile.mkdtemp(prefix="quality-verify-"))
        qlib.sh("git", "worktree", "add", "--detach", "--force", str(self.path), "HEAD")
        return self.path

    def __exit__(self, *exc) -> None:
        subprocess.run(["git", "worktree", "remove", "--force", str(self.path)], capture_output=True,
                       cwd=qlib.repo_root())


EXECUTION_NOTE = (
    "\n\n## You may execute code\nYou are in a throwaway checkout of the change; it is deleted after you "
    "finish. Run the relevant tests, or write a small reproduction test for a finding's failure scenario and "
    "run it. A reproduction that fails as the finding predicts is the strongest confirmation. A targeted test "
    "that passes is strong counter-evidence: cite the command and its output in `evidence`. If you cannot "
    "build or run anything, say so in `note` and judge from the code.")


def context_block(cfg: dict, diff_range: str, prior: list[dict], intent: str) -> str:
    skip = ", ".join(cfg["review"].get("skip_paths", []))
    prior_txt = "\n".join(f"- {p['file']}:{p['line']} {p['title']}" for p in prior) or "(none)"
    return (
        f"## Change under review\nRun `git diff {diff_range}` to see it. Read surrounding code, callers and tests as needed.\n\n"
        f"## Stated intent of the change\n{intent or '(not provided: infer from commits; do not invent requirements)'}\n\n"
        f"## Repository review rules (REVIEW.md)\n{review_md()}\n\n"
        f"## Do not comment on these paths\n{skip}\n\n"
        f"## Findings already posted in earlier rounds (do not repeat)\n{prior_txt}\n"
    )


def families(cfg: dict, depth: str = "standard") -> list[str]:
    """Model families that hunt and verify, from quality.toml [review].
    `families` (default ["claude"]) at quick/standard depth; `deep_families` (default: the
    same list) at deep depth, which CI applies to Tier 2. One family = Anthropic/OpenAI-
    style single-family review: independent fresh contexts find, a separate fresh
    context verifies. Two = cross-model, spent where the risk tier warrants it."""
    base = cfg["review"].get("families", ["claude"])
    key = "deep_families" if depth == "deep" else "families"
    fams = [f for f in cfg["review"].get(key, base) if f in ("claude", "codex")]
    if not fams:
        raise SystemExit(f"quality.toml [review].{key} must list 'claude' and/or 'codex'")
    return fams


def find(cfg: dict, ctx: str, depth: str) -> tuple[list[dict], list[str]]:
    finder = prompt_body("bug-hunter.md")
    if depth == "quick":
        finder += "\n\nQuick mode: report only findings you are near-certain are Important."
    prompt = f"{finder}\n\n{ctx}"
    candidates, errors = [], []
    with cf.ThreadPoolExecutor() as pool:
        futs = {pool.submit(run, fam, prompt, "findings.schema.json", cfg): fam for fam in families(cfg, depth)}
        for fut in cf.as_completed(futs):
            fam = futs[fut]
            try:
                res = fut.result()
                if not res.get("complete", False):
                    errors.append(f"{fam} reviewer reported an incomplete review: {res.get('summary')}")
                for f in res.get("findings", []):
                    f["_source"] = fam
                    candidates.append(f)
            except RuntimeError as e:
                errors.append(str(e))
    return candidates, errors


def dedupe(cands: list[dict]) -> list[dict]:
    out: list[dict] = []
    for c in sorted(cands, key=lambda c: SEV_RANK.get(c["severity"], 3)):
        # Merge only near-certain duplicates. Two distinct bugs a few lines apart must
        # both survive, so "same category nearby" is NOT enough.
        dup = next((o for o in out if o["file"] == c["file"] and abs(o["line"] - c["line"]) <= 3
                    and (o["bug_class"].lower() == c["bug_class"].lower()
                         or (o["line"] == c["line"] and o["category"] == c["category"])
                         or (o["line"] == c["line"] and "integrity" in {o["_source"], c["_source"]}))), None)
        if dup:
            dup["_sources"] = sorted(set(dup["_sources"]) | {c["_source"]})
            if c.get("_prior") is not None:
                dup["_prior"] = c["_prior"]
            dup["evidence"] = list(dict.fromkeys(dup["evidence"] + c["evidence"]))
        else:
            c["_sources"] = [c["_source"]]
            out.append(c)
    for i, c in enumerate(out):
        c["id"] = f"F{i + 1}"
    return out


def verify(cfg: dict, ctx: str, cands: list[dict], depth: str) -> tuple[dict, list[str]]:
    """Returns {id: [verdict, ...]}.

    Routing ([review].verifier): a named family ("claude" / "codex") verifies every
    finding in a fresh session that never saw the finder's reasoning; "other" sends each
    finding to the family that did not raise it. Deep mode always uses every family.
    Evidence (2026): a fresh same-model session verifies about as well as a top-tier
    cross-model one (Song, arXiv 2610.01471), and the cross-model direction matters
    (Xiang et al., arXiv 2607.21656: Claude reviewing Codex helped, the reverse hurt).
    Measure it on your own evals; see evals/README.md."""
    verifier = prompt_body("verifier.md")
    fams = families(cfg, depth)
    mode = cfg["review"].get("verifier", "claude")
    execute = bool(cfg["review"].get("verify_with_execution", True))
    if execute:
        verifier += EXECUTION_NOTE
    batches: dict[str, list[dict]] = {f: [] for f in fams}
    for c in cands:
        found_by = set(c["_sources"])
        if depth == "deep" or len(fams) == 1:
            targets = fams
        elif mode in fams:
            targets = [mode]
        elif not found_by & set(fams) or found_by >= set(fams):
            targets = fams
        else:
            targets = [f for f in fams if f not in found_by]
        for t in targets:
            batches[t].append(c)

    verdicts: dict[str, list[dict]] = {}
    errors: list[str] = []
    if execute:
        with ScratchWorktree() as wt:
            return _run_verifiers(cfg, ctx, verifier, batches, verdicts, errors, wt, True)
    return _run_verifiers(cfg, ctx, verifier, batches, verdicts, errors, None, False)


def _run_verifiers(cfg, ctx, verifier, batches, verdicts, errors, cwd, execute):
    with cf.ThreadPoolExecutor() as pool:
        futs = {}
        for fam, items in batches.items():
            if not items:
                continue
            public = [{k: v for k, v in c.items() if not k.startswith("_")} for c in items]
            prompt = f"{verifier}\n\n{ctx}\n\n## Candidate findings to verify\n```json\n{json.dumps(public, indent=2)}\n```"
            futs[pool.submit(run, fam, prompt, "verdicts.schema.json", cfg, cwd, execute)] = fam
        for fut in cf.as_completed(futs):
            try:
                res = fut.result()
                if not res.get("complete", False):
                    errors.append(f"{futs[fut]} verifier could not complete verification: {res.get('note', '')}")
                for v in res.get("verdicts", []):
                    v["_by"] = futs[fut]
                    verdicts.setdefault(v["id"], []).append(v)
            except RuntimeError as e:
                errors.append(str(e))
    return verdicts, errors


def decide(c: dict, vs: list[dict]) -> tuple[str, str]:
    """Combine verdicts -> (status, severity). Evidence beats votes:
    - a refutation counts only if it cites counter-evidence;
    - counted refutation(s) and no confirmation -> dropped;
    - confirmation(s) and no counted refutation -> confirmed at the verifiers' severity;
    - anything else (split, unresolved, no verdict) -> unresolved at the WORST severity
      anyone assigned, so a verifier escalating a nit to Important is never ignored."""
    worst = min([c["severity"]] + [v["severity"] for v in vs], key=lambda s: SEV_RANK[s])
    confirmed = [v for v in vs if v["verdict"] == "confirmed"]
    refuted = [v for v in vs if v["verdict"] == "refuted" and v.get("evidence")]
    if confirmed and not refuted:
        return "confirmed", min((v["severity"] for v in confirmed), key=lambda s: SEV_RANK[s])
    if refuted and not confirmed:
        return "refuted", c["severity"]
    return "unresolved", worst


def render(final: list[dict], errors: list[str], max_nits: int, sha: str, by: list[str]) -> str:
    imp = [f for f in final if f["severity"] == "important"]
    pre = [f for f in final if f["severity"] == "pre_existing"]
    nits = [f for f in final if f["severity"] == "nit"][:max_nits]
    head = (f"**{len(imp)} important, {len(pre)} pre-existing, {len(nits)} nit** "
            f"(independent review of `{sha[:10]}` by {'+'.join(by)})")
    if not final and not errors:
        return head + "\n\nNothing to fix.\n"
    lines = [head, ""]
    if errors:
        lines += ["> **Review incomplete — treated as blocking:**"] + [f"> - {e.splitlines()[0]}" for e in errors] + [""]
    tag = {"important": "🔴 Important", "pre_existing": "🟣 Pre-existing", "nit": "🟡 Nit"}
    for f in imp + pre + nits:
        status = "" if f["_status"] == "confirmed" else " · ⚠️ unresolved: prove safe with a test or fix"
        lines.append(f"### {tag[f['severity']]} · `{f['file']}:{f['line']}` · {f['category']}{status}")
        lines.append(f"{f['title']}\n\n**Scenario:** {f['failure_scenario']}\n\n**Fix:** {f['suggested_fix']}")
        lines.append(f"**Class:** {f['bug_class']} — fix every instance, not just this one.")
        lines.append("**Evidence:** " + "; ".join(f"`{e}`" for e in f["evidence"][:4]))
        lines.append(f"<sub>found by {', '.join(f['_sources'])} · verified by {', '.join(f['_verified_by']) or '—'}</sub>\n")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--since", help="review only the delta since this SHA (incremental re-review)")
    ap.add_argument("--depth", choices=["quick", "standard", "deep"])
    ap.add_argument("--intent", default="", help="task / acceptance criteria / PR description")
    args = ap.parse_args()

    cfg = qlib.load_config()
    depth = args.depth or cfg["review"].get("depth", "standard")
    sha = qlib.sh("git", "rev-parse", "HEAD").strip()
    diff_range = f"{args.since}..HEAD" if args.since else f"{qlib.merge_base(args.base)}...HEAD"
    state = qlib.state_dir()
    ledger_path = state / "ledger.json"
    prior = json.loads(ledger_path.read_text()) if ledger_path.exists() else []

    ctx = context_block(cfg, diff_range, prior, args.intent)
    cands, errors = find(cfg, ctx, depth)
    integrity = json.loads(qlib.sh("python3", str(qlib.HERE / "check_test_integrity.py"), "--base", args.base))
    cands += integrity["findings"]

    skip = cfg["review"].get("skip_paths", [])
    cands = [c for c in cands if not qlib.matches(c["file"], skip)]
    # No repeats, without ever suppressing a new candidate on the ledger's say-so (the
    # ledger is cached state and must not be able to hide a finding): new candidates and
    # re-checks of still-open findings go through the same dedupe, so a repeat merges
    # into one item instead of being posted twice. Closed findings play no part.
    open_prior = [p for p in prior if p.get("open")]
    # ...but every still-open prior finding is re-verified against HEAD. A later
    # commit that fixes it clears it; an author response is weighed by the verifier.
    responses = json.loads((state / "responses.json").read_text()) if (state / "responses.json").exists() else []
    for p in open_prior:
        f = dict(p["finding"], _source="prior", _sources=[], _prior=prior.index(p))
        reply = next((r["reason"] for r in responses
                      if r["file"] == f["file"] and r["bug_class"].lower() == f["bug_class"].lower()), None)
        f["title"] = f"[previously raised at {p['sha'][:8]} — check whether still present at HEAD] {f['title']}"
        # Untrusted text from the implementer: separate field, never part of the claim.
        f["author_response_untrusted"] = reply or ""
        cands.append(f)
    cands = dedupe(cands)

    verdicts, verrs = verify(cfg, ctx, cands, depth) if cands else ({}, [])
    errors += verrs

    final = []
    for c in cands:
        status, sev = decide(c, verdicts.get(c["id"], []))
        if status == "refuted":
            continue
        c.update(_status=status, severity=sev,
                 _verified_by=[v["_by"] for v in verdicts.get(c["id"], [])],
                 _verdicts=verdicts.get(c["id"], []))
        final.append(c)
    final.sort(key=lambda f: (SEV_RANK[f["severity"]], f["_status"] != "confirmed", f["file"], f["line"]))

    blockers = [f for f in final if f["severity"] == "important"]
    policy = qlib.policy_fingerprint(Path(cfg["_path"]), qlib.review_rules_path())
    report = {"sha": sha, "range": diff_range, "depth": depth, "policy": policy, "errors": errors,
              "blocking": bool(blockers or errors), "candidates": len(cands), "findings": final}
    qlib.write_json(state / f"review-{sha[:12]}.json", report)
    # Ledger: re-verified prior findings keep their entry (open iff still blocking);
    # everything new this round is appended.
    for p in prior:
        p["open"] = False
    for f in final:
        if f.get("_prior") is not None:
            prior[f["_prior"]]["open"] = f["severity"] == "important"
            continue
        prior.append({"file": f["file"], "line": f["line"], "title": f["title"], "bug_class": f["bug_class"],
                      "sha": sha, "open": f["severity"] == "important",
                      "finding": {k: v for k, v in f.items() if not k.startswith("_") and k != "id"}})
    qlib.write_json(ledger_path, prior)
    md = render(final, errors, cfg["review"].get("max_nits", 3), sha, families(cfg, depth))
    (state / "review.md").write_text(md)
    print(md)
    # 2 = the review itself is incomplete (tool/model failure): blocking, but repair
    # rounds can't help, so the loop stops instead of asking the implementer to "fix" it.
    raise SystemExit(2 if errors else 1 if blockers else 0)


if __name__ == "__main__":
    main()
