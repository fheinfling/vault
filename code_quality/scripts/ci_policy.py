#!/usr/bin/env python3
"""Expose the few policy values the CI workflow needs before a job can start, and run [ci].setup.

  ci_policy.py <quality.toml>               # prints KEY=VALUE lines for $GITHUB_OUTPUT
  ci_policy.py <quality.toml> --run-setup   # runs each [ci].setup command with bash -c; stops at the first failure

[ci].runs_on     runner label for the deterministic `checks` job (default ubuntu-latest). A repo whose
                 build exists on one platform only (Swift on macOS) sets it here.
[ci].setup       shell commands that install the toolchain on that runner before the gate.
[review].where   "ci": reviewers run in the workflow, which then needs API keys.
                 "local": the loop runs on a developer's machine through the logged-in CLIs, the evidence
                 is published to the PR (evidence_comment.py), and CI only verifies it. No API keys.

The file is the one trusted_kit.sh staged from the base branch. A BOOTSTRAP marker next to it means
the base had no kit and the policy came from the PR itself; that is reported as bootstrap=true.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tomllib

WHERE = ("ci", "local")


def read(path: str) -> dict:
    cfg = tomllib.loads(pathlib.Path(path).read_text())
    ci = cfg.get("ci", {})
    where = cfg.get("review", {}).get("where", "ci")
    if where not in WHERE:
        sys.exit(f"ci_policy: [review].where must be one of {WHERE}, got {where!r}")
    setup = ci.get("setup", [])
    if not isinstance(setup, list) or not all(isinstance(s, str) for s in setup):
        sys.exit("ci_policy: [ci].setup must be a list of shell command strings")
    return {"runs_on": ci.get("runs_on", "ubuntu-latest"), "setup": setup, "review_where": where,
            "bootstrap": (pathlib.Path(path).parent / "BOOTSTRAP").exists()}


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--run-setup"]
    if len(args) != 1:
        sys.exit(__doc__)
    policy = read(args[0])
    if "--run-setup" in sys.argv:
        for cmd in policy["setup"]:
            print(f"setup: {cmd}", flush=True)
            if subprocess.run(["bash", "-c", cmd]).returncode != 0:
                sys.exit(f"ci_policy: setup command failed: {cmd}")
        return
    print(f"runs_on={policy['runs_on']}")
    print(f"review_where={policy['review_where']}")
    print(f"bootstrap={'true' if policy['bootstrap'] else 'false'}")


if __name__ == "__main__":
    main()
