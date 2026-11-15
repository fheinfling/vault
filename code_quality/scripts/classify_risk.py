#!/usr/bin/env python3
"""Assign a risk tier (0/1/2) to the current branch's change.

"The change sets the tier, not the author."
  1. Deterministic floor from quality.toml path rules (cannot be lowered).
  2. Optional model classifier (--model) that may RAISE the tier, and answers a
     second question: does this need a person regardless of tier?

Usage: classify_risk.py [--base origin/main] [--model] [--config quality.toml]
Prints JSON {tier, approvals_required, reasons, floor_tier, model}. Exit 0.
"""

from __future__ import annotations

import argparse
import json
import subprocess

import qlib


def deterministic(cfg: dict, files: list[str], stats: dict) -> tuple[int, list[str], bool]:
    """-> (tier, reasons, floor_hit). floor_hit = some [[risk.floor]] rule matched."""
    risk = cfg.get("risk", {})
    tier, reasons, floor_hit = 0, [], False
    for floor in risk.get("floor", []):
        # `exclude` (optional) carves paths out of a floor, so a floor can read
        # "everything except docs and tests" instead of a list of known paths.
        hit = [f for f in files
               if qlib.matches(f, floor["paths"]) and not qlib.matches(f, floor.get("exclude", []))]
        if hit:
            reasons.append(f"T{floor['tier']}: {floor['reason']} ({', '.join(hit[:5])}{' …' if len(hit) > 5 else ''})")
            tier = max(tier, floor["tier"])
            floor_hit = True

    if tier == 0:
        t0 = risk.get("t0", {})
        outside = [f for f in files if not qlib.matches(f, t0.get("paths", []))]
        lines = sum(a + r for a, r in stats.values())
        if outside:
            tier = risk.get("default_tier", 1)
            reasons.append(f"T{tier}: default (files outside the T0 allowlist, e.g. {outside[0]})")
        elif lines > t0.get("max_changed_lines", 0):
            tier = risk.get("default_tier", 1)
            reasons.append(f"T{tier}: {lines} changed lines exceeds T0 limit")
        else:
            reasons.append("T0: docs/copy-only change within size limit")
    return tier, reasons, floor_hit


def model_opinion(cfg: dict, base: str) -> dict | None:
    schema = (qlib.KIT / "schemas" / "risk.schema.json").read_text()
    prompt_file = qlib.KIT / "agents" / "risk-classifier.md"
    prompt = prompt_file.read_text().split("---", 2)[-1]  # drop frontmatter
    prompt += f"\n\nClassify the change `git diff {qlib.merge_base(base)}...HEAD` in this repository."
    res = subprocess.run(
        ["claude", "-p", prompt, "--model", cfg["agents"]["claude_model"],
         "--output-format", "json", "--json-schema", schema,
         "--allowedTools", "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*)",
         "--permission-mode", "dontAsk"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return json.loads(res.stdout)["structured_output"]
    except (json.JSONDecodeError, KeyError, TypeError):
        qlib.eprint(f"risk classifier failed; keeping deterministic tier\n{res.stderr[-2000:]}")
        return None


def decide_tier(cfg: dict, floor: int, floor_hit: bool, model: dict | None, lines: int) -> tuple[int, list[str], bool]:
    """Combine the deterministic floor with the model's opinion -> (tier, reasons, model_t0).

    The model may raise freely. It may lower to T0 only when every one of these holds:
    [risk].allow_model_t0 is on, no [[risk.floor]] rule matched, the model does not ask
    for a human regardless, and the diff is within the T0 size limit. model_t0=True marks
    such a change in risk.json and the packet so the weekly audit can sample it."""
    tier, reasons, model_t0 = floor, [], False
    if not model:
        return tier, reasons, model_t0
    proposed = model["tier"]
    if model.get("needs_human_regardless") and proposed < 1:
        proposed = 1
    if proposed > tier:
        reasons.append(f"T{proposed}: raised by classifier: {'; '.join(model['reasons'])}")
        tier = proposed
    elif (proposed == 0 and tier > 0 and not floor_hit and cfg["risk"].get("allow_model_t0")
          and not model.get("needs_human_regardless")):
        limit = cfg["risk"].get("t0", {}).get("max_changed_lines", 0)
        if lines <= limit:
            reasons.append(f"T0: lowered by classifier (allow_model_t0, {lines} changed lines): "
                           f"{'; '.join(model['reasons'])}")
            tier, model_t0 = 0, True
        else:
            reasons.append(f"T{tier}: classifier proposed T0, but {lines} changed lines exceed the T0 limit of {limit}")
    return tier, reasons, model_t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--model", action="store_true", help="also ask the model classifier (can only raise)")
    ap.add_argument("--config")
    args = ap.parse_args()

    cfg = qlib.load_config(args.config)
    files, stats = qlib.changed_files(args.base), qlib.numstat(args.base)
    floor, reasons, floor_hit = deterministic(cfg, files, stats)
    lines = sum(a + r for a, r in stats.values())
    model = model_opinion(cfg, args.base) if args.model and files else None
    tier, extra, model_t0 = decide_tier(cfg, floor, floor_hit, model, lines)
    reasons += extra

    approvals = cfg["risk"]["approvals"][f"t{tier}"]
    out = {"sha": qlib.sh("git", "rev-parse", "HEAD").strip(), "base_sha": qlib.merge_base(args.base),
           "policy": cfg["_path"], "tier": tier, "approvals_required": approvals, "floor_tier": floor,
           "model_t0": model_t0, "reasons": reasons, "files": len(files), "model": model}
    qlib.write_json(qlib.state_dir() / "risk.json", out)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
