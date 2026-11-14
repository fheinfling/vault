#!/usr/bin/env python3
"""Required CI check: are there enough HUMAN approvals for this PR's tier, on this commit?

Rules (from Anthropic's process):
  - The change sets the tier (risk.json from classify_risk.py), not the author.
  - An agent's approval never counts as a person's. Only logins in
    quality.toml [risk].humans count; bots never do.
  - An approval covers one specific commit. Approvals on older commits don't count.
  - On an agent-written PR (label `agent-authored`), the human who directed the
    agent (the PR author, if in `humans`) counts as one reviewer, when
    director_counts_as_reviewer = true, ONLY via a review on the head commit
    whose body has `/director-approve` on its own line.
  - Open blocking findings fail the check regardless of approvals.

The tier and the number of approvals are never taken on trust from risk.json. In local
review mode that file arrives in a PR comment, computed against the PR's own quality.toml.
So the gate re-derives the deterministic floor from the policy it loaded (--config, the base
branch's copy in CI), takes max(published tier, floor), and reads the approvals for that
tier from the same policy. risk.json may only RAISE the tier; `approvals_required` in it is
ignored. The one way below the floor is a model-lowered Tier 0 that the trusted policy
itself allows (allow_model_t0, no floor rule hit, within the T0 size limit).

Usage (in GitHub Actions, with GH_TOKEN set):
  approval_gate.py --pr 123 --base <base-sha> --config _trusted/quality.toml \
      --risk "$EVIDENCE_DIR/risk.json" --review "$EVIDENCE_DIR/review-head.json"
CI sets EVIDENCE_DIR to a fresh directory under RUNNER_TEMP, outside the PR checkout.
Exit 0 = mergeable, 3 = waiting on a human (no approval yet, or the evidence is for an older
commit), 1 = blocked (open findings, wrong review depth or policy). The job fails on both 3 and 1,
but says "waiting for you" plainly for 3.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import classify_risk
import qlib

DIRECTOR_RE = re.compile(r"(?m)^/director-approve\s*$")  # own line, like a slash command


def required(cfg: dict, risk: dict, floor: int, floor_hit: bool, lines: int) -> tuple[int, int, list[str]]:
    """-> (tier, approvals needed, notes). `cfg` is the trusted policy, `risk` the published
    evidence, `floor`/`floor_hit`/`lines` from classify_risk.deterministic on the trusted policy."""
    notes = []
    published = risk.get("tier")
    if type(published) is not int or published not in (0, 1, 2):
        notes.append(f"published tier {published!r} is not 0, 1 or 2; treating it as Tier 2")
        published = 2
    limit = cfg["risk"].get("t0", {}).get("max_changed_lines", 0)
    model_t0 = (published == 0 and risk.get("model_t0") is True and cfg["risk"].get("allow_model_t0")
                and not floor_hit and lines <= limit)
    tier = published if model_t0 else max(published, floor)
    if tier != published:
        notes.append(f"published tier {published} is below the base policy's floor; using Tier {tier}")
    return tier, cfg["risk"]["approvals"][f"t{tier}"], notes


def depth_problem(tier: int, review: dict) -> str | None:
    """Tier 2 needs a deep review (every deep_families member hunts and verifies). In ci mode the
    workflow picks deep for Tier 2 itself; in local mode the published evidence must show it."""
    if tier == 2 and review.get("depth") != "deep":
        return (f"Tier 2 needs a deep review; the evidence shows depth {review.get('depth')!r}. "
                "Re-run review_loop.sh (it picks deep for Tier 2) or pass --depth deep")
    return None


def policy_problem(review: dict, config_path: str) -> str | None:
    """The review must have run under the trusted policy and rules (CI: the base branch's)."""
    cfg_dir = Path(config_path).parent
    rules = next((p for p in (cfg_dir / "REVIEW.md", cfg_dir / "code_quality" / "rules" / "REVIEW.md") if p.is_file()), None)
    expected = qlib.policy_fingerprint(Path(config_path), rules)
    if review.get("policy") != expected:
        return ("the review ran under a different policy or REVIEW.md than the base branch's "
                f"(evidence {str(review.get('policy'))[:12]}, base {expected[:12]}); re-run review_loop.sh")
    return None


WAIT, BLOCK = "wait", "block"


def exit_code(problems: list[tuple[str, str]]) -> int:
    """0 ready, 3 only waiting on a human, 1 anything blocking."""
    if any(kind == BLOCK for kind, _ in problems):
        return 1
    return 3 if problems else 0


def gh(path: str) -> dict:
    return json.loads(qlib.sh("gh", "api", path))


def gh_list(path: str) -> list:
    pages = json.loads(qlib.sh("gh", "api", "--paginate", "--slurp", path))
    return [item for page in pages for item in page]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", required=True)
    ap.add_argument("--repo", default=None, help="owner/name (defaults to gh's current repo)")
    ap.add_argument("--risk", required=True)
    ap.add_argument("--review", required=True)
    ap.add_argument("--base", required=True, help="base commit; the tier floor is recomputed against it")
    ap.add_argument("--config", help="the trusted policy (CI: the base branch's quality.toml)")
    args = ap.parse_args()

    cfg = qlib.load_config(args.config)
    humans = {h.lower() for h in cfg["risk"].get("humans", [])}
    repo = args.repo or qlib.sh("gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()
    pr = gh(f"repos/{repo}/pulls/{args.pr}")
    head = pr["head"]["sha"]
    checked_out = qlib.sh("git", "rev-parse", "HEAD").strip()
    if head != checked_out:
        print(f"❌ Not mergeable: live PR head {head[:10]} differs from the checked-out commit "
              f"{checked_out[:10]}; run the gate on the current head.")
        sys.exit(1)
    risk = json.loads(Path(args.risk).read_text())
    review = json.loads(Path(args.review).read_text())

    problems: list[tuple[str, str]] = []
    if review.get("sha") != head:
        problems.append((WAIT, f"review is for {review.get('sha', '?')[:10]}, head is {head[:10]} — re-run the review and publish it"))
    if risk.get("sha") != head:
        problems.append((WAIT, f"risk tier is for {str(risk.get('sha'))[:10]}, head is {head[:10]} — re-run the review and publish it"))
    if review.get("blocking"):
        n = sum(1 for f in review["findings"] if f["severity"] == "important")
        problems.append((BLOCK, f"{n} open Important finding(s) or an incomplete review"))

    stats = qlib.numstat(args.base)
    floor, _, floor_hit = classify_risk.deterministic(cfg, qlib.changed_files(args.base), stats)
    tier, need, notes = required(cfg, risk, floor, floor_hit, sum(a + r for a, r in stats.values()))
    for note in notes:
        print(f"note: {note}")
    if (problem := depth_problem(tier, review)):
        problems.append((BLOCK, problem))
    if (problem := policy_problem(review, cfg["_path"])):
        problems.append((BLOCK, problem))
    approvers = set()
    for r in gh_list(f"repos/{repo}/pulls/{args.pr}/reviews?per_page=100"):
        user = r.get("user") or {}  # null for deleted accounts
        login = user.get("login", "").lower()
        if r["state"] == "APPROVED" and r["commit_id"] == head and user.get("type") != "Bot" and login in humans:
            approvers.add(login)
    # Director sign-off. Best setup: agents open PRs under a bot identity, and the
    # human who directed them approves normally (counted above). When the agent works
    # under the human's own account (solo setups), GitHub won't let the author approve,
    # so the author submits a review on the HEAD commit whose body contains
    # "/director-approve". Never implicit: no review on this commit, no count.
    # Agents are denied `gh pr review` and the marker string by the PreToolUse hook.
    author = pr["user"]["login"].lower()
    labels = {l["name"] for l in pr.get("labels", [])}
    if cfg["risk"].get("director_counts_as_reviewer") and "agent-authored" in labels and author in humans:
        for r in gh_list(f"repos/{repo}/pulls/{args.pr}/reviews?per_page=100"):
            if ((r.get("user") or {}).get("login", "").lower() == author and r["commit_id"] == head
                    and DIRECTOR_RE.search(r.get("body") or "")):
                approvers.add(f"{author} (director)")
    if len(approvers) < need:
        problems.append((WAIT, f"Tier {tier} needs {need} human approval(s) on {head[:10]}; have {len(approvers)}"
                               + (f": {', '.join(sorted(approvers))}" if approvers else "")))

    code = exit_code(problems)
    if code:
        print(("❌ Not mergeable:" if code == 1 else "⏳ Waiting on a human:") + "\n"
              + "\n".join(f"- {p}" for _, p in problems))
        sys.exit(code)
    print(f"✅ Tier {tier}: {len(approvers)}/{need} human approval(s), no open blockers, review matches head.")


if __name__ == "__main__":
    main()
