#!/usr/bin/env python3
"""Install or update the code_quality kit in a target repo. Idempotent; safe to re-run for updates.

  python3 /path/to/code_quality/install.py <target-repo> [--profile team|solo] [--dry-run]

Deterministic steps only: copy, create-if-missing, merge, append. Files you are meant to make
repo-specific (quality.toml, AGENTS.md, CLAUDE.md, REVIEW.md, the PR template, CODEOWNERS) are
created when missing and never overwritten. Kit-owned files (code_quality/, the subagents, the
workflow) are always brought up to date; the repo's own eval cases are kept. It ends with the
checklist of what is still a person's, or the installing agent's (INSTALL.md), to do.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
KEEP_IN_TARGET_KIT = ("evals/cases/", "evals/results/")  # the repo's own cases and runs survive an update
SUBAGENTS = ("bug-hunter.md", "verifier.md", "risk-classifier.md")


def internal_cases() -> set[str]:
    """Seed cases that review the kit's own history (`repo` resolves to the repo this kit lives in).
    Their commits exist nowhere else, so they stay in the kit's source repository and are not installed."""
    out = set()
    for case in (KIT / "evals" / "cases").glob("*/case.json"):
        try:
            repo = json.loads(case.read_text()).get("repo")
        except json.JSONDecodeError:
            continue
        if repo and not Path(repo).expanduser().is_absolute() and (case.parent / repo).resolve() == KIT.parent.resolve():
            out.add(case.parent.name)
    return out


def kit_ignore():
    return shutil.ignore_patterns("__pycache__", "results", ".quality", *sorted(internal_cases()))


class Installer:
    def __init__(self, target: Path, profile: str, dry_run: bool):
        self.target, self.profile, self.dry_run = target, profile, dry_run
        self.done: list[str] = []
        self.kept: list[str] = []

    def act(self, what: str, fn) -> None:
        self.done.append(what)
        if not self.dry_run:
            fn()

    # -- steps -------------------------------------------------------------------------------
    def copy_kit(self) -> None:
        dst = self.target / "code_quality"
        if dst.resolve() == KIT.resolve():
            self.kept.append("code_quality/ (installer runs from the target's own copy; nothing to copy)")
            return

        def do() -> None:
            if dst.exists():  # drop kit files that no longer exist upstream, except the repo's own cases/results
                for name in internal_cases():  # source-only cases an older installer copied in
                    shutil.rmtree(dst / "evals" / "cases" / name, ignore_errors=True)
                for p in sorted(dst.rglob("*"), reverse=True):
                    rel = p.relative_to(dst).as_posix()
                    if any(rel.startswith(k) or rel + "/" == k for k in KEEP_IN_TARGET_KIT):
                        continue
                    if p.is_file() and not (KIT / rel).exists():
                        p.unlink()
                    elif p.is_dir() and not any(p.iterdir()):
                        p.rmdir()
            shutil.copytree(KIT, dst, dirs_exist_ok=True, ignore=kit_ignore())
        self.act("code_quality/ copied (kit files updated; your evals/cases kept; source-only seed cases left out)", do)

    def create_if_missing(self, rel: str, src: Path) -> None:
        dst = self.target / rel
        if dst.exists():
            self.kept.append(f"{rel} (exists; left as is)")
            return
        self.act(f"{rel} created from {src.relative_to(KIT)}", lambda: (dst.parent.mkdir(parents=True, exist_ok=True), shutil.copy2(src, dst)))

    def overwrite(self, rel: str, src: Path) -> None:
        dst = self.target / rel
        self.act(f"{rel} {'updated' if dst.exists() else 'installed'} (kit-owned)",
                 lambda: (dst.parent.mkdir(parents=True, exist_ok=True), shutil.copy2(src, dst)))

    def gitignore(self) -> None:
        gi = self.target / ".gitignore"
        lines = gi.read_text().splitlines() if gi.exists() else []
        if any(l.strip() in (".quality/", ".quality", "/.quality/", "/.quality") for l in lines):
            self.kept.append(".gitignore already ignores .quality/")
            return
        self.act(".gitignore: added .quality/", lambda: gi.write_text("\n".join(lines + [".quality/"]) + "\n"))

    def settings(self) -> None:
        kit = json.loads((KIT / "hooks" / "settings.json").read_text())
        kit.pop("$comment", None)
        dst = self.target / ".claude" / "settings.json"
        if not dst.exists():
            self.act(".claude/settings.json created (hooks + deny rules)",
                     lambda: (dst.parent.mkdir(parents=True, exist_ok=True), dst.write_text(json.dumps(kit, indent=2) + "\n")))
            return
        existing = json.loads(dst.read_text())
        merged = merge_settings(existing, kit)
        if merged == existing:
            self.kept.append(".claude/settings.json already has the kit's hooks and deny rules")
            return
        self.act(".claude/settings.json merged (kit hooks + deny rules added; yours kept)",
                 lambda: dst.write_text(json.dumps(merged, indent=2) + "\n"))

    def run(self) -> None:
        self.copy_kit()
        policy = KIT / ("quality.toml" if self.profile == "team" else "profiles/solo.toml")
        self.create_if_missing("quality.toml", policy)
        for name in ("AGENTS.md", "CLAUDE.md", "REVIEW.md"):
            self.create_if_missing(name, KIT / "rules" / name)
        for name in SUBAGENTS:
            self.overwrite(f".claude/agents/{name}", KIT / "agents" / name)
        self.overwrite(".github/workflows/quality-gate.yml", KIT / "ci" / "quality-gate.yml")
        self.create_if_missing(".github/PULL_REQUEST_TEMPLATE.md", KIT / "templates" / "PULL_REQUEST_TEMPLATE.md")
        if self.profile == "team":
            self.create_if_missing(".github/CODEOWNERS", KIT / "templates" / "CODEOWNERS")
        self.gitignore()
        self.settings()


def merge_settings(existing: dict, kit: dict) -> dict:
    """Union of deny rules; kit hook entries appended unless an entry with the same commands exists,
    in which case that entry takes the kit's matcher."""
    out = json.loads(json.dumps(existing))
    deny = out.setdefault("permissions", {}).setdefault("deny", [])
    for rule in kit.get("permissions", {}).get("deny", []):
        if rule not in deny:
            deny.append(rule)
    hooks = out.setdefault("hooks", {})
    for event, entries in kit.get("hooks", {}).items():
        have = hooks.setdefault(event, [])
        present = {h.get("command") for e in have for h in e.get("hooks", [])}
        for entry in entries:
            kit_cmds = {h.get("command") for h in entry.get("hooks", [])}
            if not kit_cmds <= present:
                have.append(entry)
                continue
            # Already installed: keep it, but bring the kit's matcher along (it widens over time,
            # e.g. to cover MCP tools); entries that hold none of the kit's commands are untouched.
            for e in have:
                if "matcher" in entry and kit_cmds & {h.get("command") for h in e.get("hooks", [])}:
                    e["matcher"] = entry["matcher"]
    return out


def checklist(profile: str) -> str:
    solo = profile == "solo"
    lines = [
        "Still to do (the installing agent does 1-3 and 5, see code_quality/INSTALL.md; a person does 4):",
        "  1. quality.toml: fill [checks].fast and [checks].full with this repo's real lint/type/test/build",
        "     commands (an empty gate fails on purpose); add this repo's sensitive paths to [[risk.floor]];",
        "     put the human GitHub login(s) in [risk].humans.",
        "  2. REVIEW.md: replace the placeholder with 3-6 lines on what failure looks like in THIS repo.",
        "  3. CLAUDE.md: if the repo already had one, add `@AGENTS.md` and the verify commands from",
        "     code_quality/rules/CLAUDE.md; fill the single-test command and the 'where the code misleads' lines.",
        "  4. GitHub (admin): secret ANTHROPIC_API_KEY (and OPENAI_API_KEY while deep_families has codex);",
        "     labels agent-authored, risk:t0, risk:t1, risk:t2; ruleset on main: require PRs, require the",
        "     `checks` and `quality-gate` status checks, dismiss stale approvals on push, merge queue.",
    ]
    if solo:
        lines += ["     Solo: skip 'require review from code owners' unless agents push under a bot identity;",
                  "     you cannot code-owner-approve your own PR. The local hook still blocks agent edits to the workflow."]
    else:
        lines += ["     Team: edit .github/CODEOWNERS (replace @your-org/quality-owners with 2+ humans) and turn on",
                  "     'require review from code owners'."]
    lines += ["  5. Verify: code_quality/scripts/quality_gate.sh full is green; python3 -m unittest discover -s code_quality/tests;",
              "     python3 code_quality/scripts/flow_graph.py validate.",
              "  6. Start in shadow mode (FLOW.md 'Earning autonomy'): two weeks of reviewing as today, then tiered approvals."]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="path to the repo to install into (its git root)")
    ap.add_argument("--profile", choices=["team", "solo"], default="team")
    ap.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    args = ap.parse_args()

    target = Path(args.target).expanduser().resolve()
    if not (target / ".git").exists():
        sys.exit(f"install: {target} is not a git repository root")
    inst = Installer(target, args.profile, args.dry_run)
    inst.run()
    head = f"code_quality install ({args.profile} profile){' [dry run]' if args.dry_run else ''} -> {target}"
    print(head)
    for d in inst.done:
        print(f"  + {d}")
    for k in inst.kept:
        print(f"  = {k}")
    print()
    print(checklist(args.profile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
