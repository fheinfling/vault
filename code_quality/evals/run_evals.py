#!/usr/bin/env python3
"""Score the reviewer against curated cases (see evals/README.md).

Usage: run_evals.py <cases_dir> [--kind incident|clean|adversarial|rule] [--only <id>] [--depth standard]

A case points at a real repo and two commits, or carries a small `fixture` (base and
head file trees) that becomes a throwaway git repo on the fly. Either way the reviewer
that runs is the one configured NOW (see install_kit), scored against findings a human
adjudicated once when the case was added.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
POLICY_FILES = ("quality.toml", "REVIEW.md")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def install_kit(wt: Path) -> Path:
    """Stage the CURRENT review system for a case's worktree; returns the directory it runs from.

    The kit is copied to a directory OUTSIDE the worktree and run from there, as CI runs
    it from `_trusted/`. The worktree keeps whatever historical `code_quality/` the case
    commit carries: that is code under review (two seed cases review bugs in this very
    kit), not the reviewer. The policy files (quality.toml, REVIEW.md) are read from the
    repo root, so the installing repo's current ones are overlaid into the worktree; when
    that repo has none, a historical copy is removed so the kit's own defaults apply.
    Known limit: a case whose change edits REVIEW.md or quality.toml is verified against
    the overlaid version. Evals score today's reviewer against yesterday's bug, never the
    reviewer that shipped with the bug."""
    kit_dir = Path(tempfile.mkdtemp(prefix=f"{wt.name}-kit-")) / "code_quality"
    shutil.copytree(KIT, kit_dir, ignore=shutil.ignore_patterns("results", "cases", "__pycache__"))
    for name in POLICY_FILES:
        current, historical = KIT.parent / name, wt / name
        if current.is_file():
            shutil.copy2(current, historical)
        elif historical.exists():
            historical.unlink()  # so qlib falls back to the kit's own copy
    return kit_dir


def materialize_fixture(fixture: dict) -> tuple[Path, str, str]:
    """Build a throwaway repo from {"base": {path: content}, "head": {path: content | null}}.

    `base` is committed first; `head` is applied on top of it (a string replaces the
    file, null deletes it) and committed. Returns (repo, base_sha, head_sha). The
    caller removes the repo."""
    repo = Path(tempfile.mkdtemp(prefix="eval-fixture-"))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "evals@example.com")
    git(repo, "config", "user.name", "evals")

    def apply(files: dict) -> None:
        for rel, content in files.items():
            p = repo / rel
            if content is None:
                p.unlink(missing_ok=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)

    apply(fixture["base"])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    apply(fixture.get("head", {}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", "change under review")
    return repo, base, git(repo, "rev-parse", "HEAD")


def resolve_repo(case: dict) -> Path:
    """`repo` may be relative to the case's directory: "../../../.." is the repo this kit lives in."""
    p = Path(case["repo"]).expanduser()
    return p if p.is_absolute() else (case["_dir"] / p).resolve()


def hit(expect: dict, f: dict) -> bool:
    lo, hi = expect.get("line_range", [0, 10**9])
    text = f"{f['title']} {f['failure_scenario']} {f['bug_class']}".lower()
    return (f["file"] == expect["file"] and lo <= f["line"] <= hi
            and f["severity"] == expect.get("severity", "important")
            and (not expect.get("keywords") or any(k.lower() in text for k in expect["keywords"])))


def run_case(case: dict, depth: str) -> dict:
    repo = fixture_repo = wt = kit_dir = None
    try:
        if "fixture" in case:
            fixture_repo, base, head = materialize_fixture(case["fixture"])
            repo = fixture_repo
        else:
            repo, base, head = resolve_repo(case), case["base"], case["head"]
        wt = Path(tempfile.mkdtemp(prefix=f"eval-{case['id']}-"))
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), head],
                       check=True, capture_output=True)
        kit_dir = install_kit(wt)
        res = subprocess.run([sys.executable, str(kit_dir / "scripts" / "cross_review.py"),
                              "--base", base, "--depth", depth, "--intent", case.get("intent", "")],
                             cwd=wt, capture_output=True, text=True)
        sha = git(wt, "rev-parse", "HEAD")
        report = json.loads((wt / ".quality" / f"review-{sha[:12]}.json").read_text())
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as e:
        detail = e.stderr.strip() if isinstance(e, subprocess.CalledProcessError) and e.stderr else ""
        return {"id": case["id"], "kind": case["kind"], "error": f"{e} {detail}".strip()}
    finally:
        if wt is not None and repo is not None:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        if fixture_repo is not None:
            shutil.rmtree(fixture_repo, ignore_errors=True)
        if kit_dir is not None:
            shutil.rmtree(kit_dir.parent, ignore_errors=True)

    important = [f for f in report["findings"] if f["severity"] == "important"]
    caught = [e for e in case["expect"] if any(hit(e, f) for f in report["findings"])]
    # Conditional catches: which family found each real bug. This is the evidence
    # for (or against) spending a second model family (run with --depth deep).
    by_family = {"claude_only": 0, "codex_only": 0, "both": 0, "deterministic_only": 0}
    for e in caught:
        srcs = set()
        for f in report["findings"]:
            if hit(e, f):
                srcs |= set(f.get("_sources", []))
        fams = srcs & {"claude", "codex"}
        key = "both" if len(fams) == 2 else (f"{fams.pop()}_only" if fams else "deterministic_only")
        by_family[key] += 1
    unexpected = [f for f in important if not any(hit(e, f) for e in case["expect"])]
    return {"id": case["id"], "kind": case["kind"], "expected": len(case["expect"]), "caught": len(caught),
            "unexpected_important": len(unexpected), "by_family": by_family,
            "unexpected": [f"{f['file']}:{f['line']} {f['title']}" for f in unexpected],
            "errors": report["errors"], "exit": res.returncode}


def load_cases(cases_dir: str) -> list[dict]:
    cases = []
    for p in sorted(Path(cases_dir).glob("*/case.json")):
        c = json.loads(p.read_text())
        c["_dir"] = p.parent
        cases.append(c)
    return cases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases_dir")
    ap.add_argument("--kind")
    ap.add_argument("--only", help="run a single case id")
    ap.add_argument("--depth", default="standard")
    args = ap.parse_args()

    cases = load_cases(args.cases_dir)
    cases = [c for c in cases if (not args.kind or c["kind"] == args.kind) and (not args.only or c["id"] == args.only)]
    if not cases:
        sys.exit("no cases found")
    results = []
    for c in cases:
        r = run_case(c, args.depth)
        results.append(r)
        if "error" in r:
            print(f"ERROR  {r['id']}: {r['error'][:300]}", file=sys.stderr)
        else:
            print(f"{r['kind']:11} {r['id']:36} caught {r['caught']}/{r['expected']}  "
                  f"unexpected-important {r['unexpected_important']}  errors {len(r['errors'])}", file=sys.stderr)

    scored = [r for r in results if "error" not in r]
    exp = sum(r["expected"] for r in scored if r["kind"] != "clean")
    got = sum(r["caught"] for r in scored if r["kind"] != "clean")
    clean = [r for r in scored if r["kind"] == "clean"]
    summary = {
        "recall": round(got / exp, 3) if exp else None,
        "noise_per_clean_pr": round(sum(r["unexpected_important"] for r in clean) / len(clean), 3) if clean else None,
        "cases": len(cases), "errors": len(results) - len(scored),
        "conditional_catches": {k: sum(r.get("by_family", {}).get(k, 0) for r in scored)
                                for k in ("claude_only", "codex_only", "both", "deterministic_only")},
    }
    out_dir = KIT / "evals" / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{stamp}.json").write_text(json.dumps({"summary": summary, "results": [
        {k: v for k, v in r.items() if k != "_dir"} for r in results]}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
