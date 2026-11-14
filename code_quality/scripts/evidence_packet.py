#!/usr/bin/env python3
"""Build the one page a human reads (T1/T2) — or the record nobody needs to read (T0).

The point is to make human review fast: what changed, why it is safe, what was
checked and by whom, what is still open, and how to undo it. Reading evidence
is faster than re-running checks.

Usage: evidence_packet.py [--base origin/main] [--rounds N] [--status converged|escalated]
Writes .quality/packet.md and prints it.
"""

from __future__ import annotations

import argparse
import json

import qlib


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--status", default="converged")
    args = ap.parse_args()

    st = qlib.state_dir()
    sha = qlib.sh("git", "rev-parse", "HEAD").strip()
    risk = json.loads((st / "risk.json").read_text()) if (st / "risk.json").exists() else {}
    review_md = (st / "review.md").read_text() if (st / "review.md").exists() else "_no review run_"
    stat = qlib.sh("git", "diff", "--stat", f"{qlib.merge_base(args.base)}...HEAD").strip().splitlines()
    log = qlib.sh("git", "log", "--oneline", f"{qlib.merge_base(args.base)}..HEAD").strip()
    gate_logs = sorted(st.glob("gate-*.log"))

    tier = risk.get("tier", "?")
    need = risk.get("approvals_required", "?")
    lines = [f"# Evidence packet · `{sha[:10]}` · Tier {tier} · {need} human approval(s) required", ""]
    if args.status == "escalated":
        lines += ["> ⛔ **Escalated:** the review loop hit its round cap without converging. "
                  "Before another round, step back: is the approach right? Remaining findings below.", ""]
    elif tier == 0:
        lines += ["> ✅ Tier 0: no human approval needed once CI and security checks are green. "
                  "This packet is the audit record.", ""]
        if risk.get("model_t0"):
            lines += ["> 🔎 Lowered to Tier 0 by the classifier (additive code behind a default-off flag, "
                      "see its reasons above). Include this merge in the weekly Tier 0 sample.", ""]
    lines += ["## Risk", *[f"- {r}" for r in risk.get("reasons", ["not classified"])], ""]
    lines += ["## Change", f"`{stat[-1] if stat else 'no diff'}`", "", "```", log, "```", ""]
    lines += ["## Gate", f"Full quality gate ran {len(gate_logs)} check(s); all green before review."
              if args.status == "converged" else "See rounds log.", ""]
    lines += [f"## Independent review ({args.rounds} round(s))", review_md, ""]
    lines += ["## For the human reviewer (only if tier ≥ 1)",
              "- [ ] Intent: does this do what was asked, and nothing else?",
              "- [ ] Approach: would you want other code to copy this pattern?",
              "- [ ] Risk: is the tier right, and is the rollout/rollback plan in the PR real?",
              "Approve the **head commit** only; any new commit needs re-approval.", ""]
    md = "\n".join(lines)
    (st / "packet.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
