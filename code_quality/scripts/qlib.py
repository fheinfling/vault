"""Shared helpers for the code-quality scripts. Stdlib only (Python >= 3.11)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent  # code_quality/


def sh(*args: str, check: bool = True, cwd: str | None = None) -> str:
    res = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    if check and res.returncode != 0:
        sys.exit(f"command failed ({res.returncode}): {' '.join(args)}\n{res.stderr}")
    return res.stdout


def repo_root() -> Path:
    return Path(sh("git", "rev-parse", "--show-toplevel").strip())


def load_config(path: str | None = None) -> dict:
    """quality.toml: an explicit path, else $QUALITY_POLICY (review_loop.sh points it at the base
    branch's copy), else the repo root, falling back to the kit's copy."""
    path = path or os.environ.get("QUALITY_POLICY") or None
    candidates = [Path(path)] if path else [repo_root() / "quality.toml", KIT / "quality.toml"]
    for c in candidates:
        if c.is_file():
            with open(c, "rb") as fh:
                cfg = tomllib.load(fh)
            cfg["_path"] = str(c)
            return cfg
    sys.exit("quality.toml not found (looked in repo root and code_quality/)")


@lru_cache(maxsize=None)
def _glob_re(pattern: str) -> re.Pattern:
    """Glob -> regex. `**/` = zero or more dirs, `**` = anything, `*` = within a segment.
    A pattern without '/' matches the basename at any depth (gitignore-style)."""
    if "/" not in pattern:
        pattern = "**/" + pattern
    i, out = 0, ""
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += r"(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += r".*"
            i += 2
        elif pattern[i] == "*":
            out += r"[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += r"[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(rf"^{out}$")


def matches(path: str, patterns: list[str]) -> bool:
    return any(_glob_re(p).match(path) for p in patterns)


def merge_base(base: str) -> str:
    return sh("git", "merge-base", base, "HEAD").strip()


def changed_files(base: str) -> list[str]:
    out = sh("git", "diff", "--name-only", "--no-renames", f"{merge_base(base)}...HEAD")
    return [l for l in out.splitlines() if l]


def numstat(base: str) -> dict[str, tuple[int, int]]:
    out = sh("git", "diff", "--numstat", "--no-renames", f"{merge_base(base)}...HEAD")
    stats = {}
    for line in out.splitlines():
        add, rem, path = line.split("\t", 2)
        stats[path] = (int(add) if add.isdigit() else 0, int(rem) if rem.isdigit() else 0)
    return stats


def state_dir() -> Path:
    """Per-branch working state (gitignored): findings ledger, round counter."""
    d = repo_root() / ".quality"
    d.mkdir(exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n")
    return d


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n")


def eprint(*a) -> None:
    print(*a, file=sys.stderr)


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def review_rules_path() -> Path | None:
    """REVIEW.md the reviewers read: $QUALITY_REVIEW_MD (base branch's copy, set by review_loop.sh),
    else the repo root's, else the kit's template."""
    env = os.environ.get("QUALITY_REVIEW_MD")
    for p in ([Path(env)] if env else []) + [repo_root() / "REVIEW.md", KIT / "rules" / "REVIEW.md"]:
        if p.is_file():
            return p
    return None


def policy_fingerprint(policy_toml: Path, review_md: Path | None) -> str:
    """sha256 over the policy and review rules a review ran under. The gate recomputes it from the
    base branch's copies, so evidence produced under any other policy is rejected."""
    h = hashlib.sha256(Path(policy_toml).read_bytes())
    h.update(b"\0")
    h.update(Path(review_md).read_bytes() if review_md and Path(review_md).is_file() else b"")
    return h.hexdigest()
