#!/usr/bin/env python3
"""Deterministic detector for check-weakening ("make the gate green by moving the gate").

Agents under pressure to pass sometimes delete assertions, skip tests, add
suppressions, or loosen thresholds. This script does not decide whether a
weakening is legitimate; it emits candidate findings that go to the verifier,
which must confirm the task justifies them. Unjustified weakening blocks.

Usage: check_test_integrity.py [--base origin/main]
Prints findings JSON in the findings.schema.json shape.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict

import qlib

TEST_FILE = ["**/test/**", "**/tests/**", "**/__tests__/**", "**/spec/**", "*.test.*", "*.spec.*",
             "test_*.py", "*_test.py", "*_test.go", "*Test.java", "*Tests.cs", "*_spec.rb"]
SNAPSHOT = ["**/__snapshots__/**", "*.snap", "**/golden/**", "*.golden"]
TEST_CONFIG = ["jest.config.*", "vitest.config.*", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini",
               ".coveragerc", "karma.conf.*", "playwright.config.*", ".github/workflows/**", ".eslintrc*",
               "eslint.config.*", "tsconfig*.json", "mypy.ini", ".golangci.*", "ruff.toml", "biome.json",
               "package.json", "Makefile", "justfile", "Taskfile.yml", "noxfile.py", "Rakefile"]

ASSERT = re.compile(r"\b(assert\w*|expect\s*\(|should\.|\.to(Be|Equal|Throw|Match|Have)\w*|require\.\w+\(|t\.(Error|Fatal|Fail)\w*\()")
SKIP = re.compile(r"(\b(it|test|describe)\.(skip|only|todo)\b|\bx(it|describe|test)\s*\(|@pytest\.mark\.(skip|xfail)|"
                  r"@unittest\.skip|\bt\.Skip(Now|f)?\(|#\[ignore\]|@Disabled|@Ignore\b|pytest\.skip\()")
# Directives that hide a checker's verdict. Type loosening such as `as any` or `catch (e: any)` is a
# code-quality matter for the reviewers, not a gate bypass: flagging it here turned every such line
# into an Important candidate that the verifier is told to confirm.
SUPPRESS = re.compile(r"(eslint-disable|@ts-ignore|@ts-nocheck|@ts-expect-error|#\s*type:\s*ignore|#\s*noqa|"
                      r"//\s*nolint|#\[allow\(|@SuppressWarnings|# pragma: no cover|istanbul ignore)")
THRESHOLD = re.compile(r"(coverageThreshold|fail_under|cov-fail-under|branches|functions|statements|lines)\s*[:=]")
# Gate commands in quality.toml come from the base branch, but they call scripts the PR controls
# (e.g. `npm test` reads the PR's package.json). Redefining those scripts can turn the gate into a no-op.
# Script / target names only: `test`, `test:ci`, `lint:fix`, `build`. The name ends right after the
# keyword or a `:`-separated suffix; a looser `[\w:-]*` flagged testTimeout, testEnvironment,
# checkJs and build-backend as a redefined gate.
GATE_SCRIPT = re.compile(r'^\s*"?(test|lint|typecheck|type-check|check|build|ci|verify)(?::[\w-]+)*"?\s*[:=]|^(test|lint|check|build|ci)\s*:')
DISCOVERY = re.compile(r"(testMatch|testPathIgnore|testpaths|--ignore|--deselect|exclude|testRegex|-k\s)")
# The kit's own scripts, tests and hooks are full of the patterns above (they define them). Changes
# there are Tier 2 by policy and reviewed as such; scanning them only produced noise on every install.
KIT_PATHS = ["code_quality/**"]


def finding(file: str, line: int, title: str, scenario: str, evidence: list[str], cls: str) -> dict:
    return {"severity": "important", "category": "test-integrity", "file": file, "line": line,
            "title": title, "failure_scenario": scenario, "evidence": evidence,
            "suggested_fix": "Restore the check, or justify the change in the PR description with the requirement that made it necessary.",
            "bug_class": cls, "_source": "integrity"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    args = ap.parse_args()

    mb = qlib.merge_base(args.base)
    diff = qlib.sh("git", "diff", "-U0", "--no-renames", f"{mb}...HEAD")
    deleted = qlib.sh("git", "diff", "--name-only", "--diff-filter=D", f"{mb}...HEAD").split()

    out: list[dict] = []
    for f in deleted:
        if qlib.matches(f, KIT_PATHS):
            continue
        if qlib.matches(f, TEST_FILE):
            out.append(finding(f, 1, f"Test file {f} was deleted.",
                               "Behavior it covered is no longer checked; a regression would pass CI.",
                               [f"git diff --diff-filter=D: {f}"], "deleted test"))

    file, old_file, new_line, in_header = None, None, 0, False
    asserts = defaultdict(lambda: [0, 0, 0])  # file -> [removed, added, first_line]
    for raw in diff.splitlines():
        # File headers only occur between `diff --git` and the first `@@`; tracking that
        # keeps a removed SQL comment ("-- x" -> "--- x") from being read as a header.
        if raw.startswith("diff --git "):
            in_header, file, old_file = True, None, None
            continue
        if in_header:
            if raw.startswith("--- "):
                old_file = raw[6:] if raw.startswith("--- a/") else None
            elif raw.startswith("+++ "):
                file = raw[6:] if raw.startswith("+++ b/") else None
            elif raw.startswith("@@"):
                in_header = False
            if in_header:
                continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            new_line = int(m.group(1)) if m else 0
            continue
        sign, text = raw[:1], raw[1:]
        path = old_file if sign == "-" else file
        if not path or sign not in ("+", "-"):
            continue
        if qlib.matches(path, KIT_PATHS):
            if sign == "+":
                new_line += 1
            continue
        is_test, is_cfg = qlib.matches(path, TEST_FILE), qlib.matches(path, TEST_CONFIG)
        if sign == "-" and is_cfg and (THRESHOLD.search(text) or DISCOVERY.search(text) or GATE_SCRIPT.search(text)):
            out.append(finding(path, max(new_line, 1), "Test/coverage/lint configuration removed.",
                               "A threshold, test path, or gate script was deleted; the bar may be lowered or tests may stop running.",
                               [f"{path} (removed): {text.strip()[:160]}"], "weakened gate config"))
        if is_test and ASSERT.search(text):
            a = asserts[path]
            a[0 if sign == "-" else 1] += 1
            a[2] = a[2] or new_line
        if sign == "+":
            if is_test and SKIP.search(text):
                out.append(finding(file, new_line, "Test skipped / focused / marked xfail.",
                                   "The skipped test no longer guards its behavior (or .only silently drops the rest of the suite).",
                                   [f"{file}:{new_line}: {text.strip()[:160]}"], "skipped test"))
            if SUPPRESS.search(text):
                out.append(finding(file, new_line, "New lint/type/coverage suppression added.",
                                   "A real error reported by the checker is hidden instead of fixed.",
                                   [f"{file}:{new_line}: {text.strip()[:160]}"], "suppressed diagnostic"))
            if is_cfg and GATE_SCRIPT.search(text):
                out.append(finding(file, new_line, "A script the quality gate runs was redefined.",
                                   "The gate command now runs different code than on main; it may no longer test anything.",
                                   [f"{file}:{new_line}: {text.strip()[:160]}"], "redefined gate script"))
            elif is_cfg and (THRESHOLD.search(text) or DISCOVERY.search(text)):
                out.append(finding(file, new_line, "Test/coverage/lint configuration changed.",
                                   "Thresholds or test discovery changed; tests may stop running or the bar may be lowered.",
                                   [f"{file}:{new_line}: {text.strip()[:160]}"], "weakened gate config"))
            new_line += 1

    for f, (removed, added, ln) in asserts.items():
        if removed > added:
            out.append(finding(f, ln or 1, f"Net {removed - added} assertion(s) removed from {f}.",
                               "Fewer behaviors are checked than before; a regression in them would pass.",
                               [f"{f}: -{removed} / +{added} assertion lines"], "removed assertions"))

    snaps = [f for f in qlib.changed_files(args.base) if qlib.matches(f, SNAPSHOT) and not qlib.matches(f, KIT_PATHS)]
    if snaps:
        out.append(finding(snaps[0], 1, f"{len(snaps)} snapshot/golden file(s) updated.",
                           "Regenerating snapshots accepts whatever the code now outputs, including a regression.",
                           snaps[:10], "regenerated snapshot"))

    print(json.dumps({"complete": True, "summary": f"{len(out)} integrity signal(s).", "findings": out}, indent=2))


if __name__ == "__main__":
    main()
