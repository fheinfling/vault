#!/usr/bin/env python3
"""Carry local review evidence to the PR, and back. For [review].where = "local".

  evidence_comment.py publish --pr N [--approve] [--repo owner/name]
      Posts .quality/packet.md plus the machine-readable risk.json and review-<head>.json as one
      PR comment. Run it after review_loop.sh converged, from the same checkout, with the head
      commit already pushed. --approve also submits your `/director-approve` review on that
      commit, so one command is the whole human step.

  evidence_comment.py fetch --pr N --sha <head> [--repo owner/name] [--config quality.toml] [--out .quality]
      CI: finds the newest evidence comment for that head commit, posted by a human listed in
      [risk].humans, and writes risk.json and review-head.json for approval_gate.py.
      No such comment: exit 3 ("waiting"), nothing written. The gate stays closed.

Trust: only comments by a human from [risk].humans count; bots and other accounts are ignored.
The evidence itself was produced by the loop on that person's machine, so local mode rests on
that machine and that person, who is also where the tier's human approval comes from. Publishing
is a human step: the PreToolUse hook blocks agents from running `publish`, from posting a comment
with the evidence marker, and from editing the files in .quality/ that the loop wrote. In a setup
where the agent uses the human's GitHub account, that hook is the only thing telling them apart, so
it is a guardrail, not a proof; ci mode avoids the question entirely.
The published tier is not trusted: risk.json was computed against the PR's own quality.toml, so
approval_gate.py re-derives the floor and the approval count from the base policy and lets the
evidence only raise the tier.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

import qlib

MARKER = "<!-- quality-evidence sha={sha} -->"
MARKER_RE = re.compile(r"<!-- quality-evidence sha=([0-9a-f]{40}) -->")
JSON_BLOCK_RE = re.compile(r"```json\n(\{.*?\})\n```", re.S)


def render(packet_md: str, risk: dict, review: dict) -> str:
    """The comment body: marker, the human-readable packet, then the JSON the gate needs."""
    payload = json.dumps({"risk": risk, "review": review}, sort_keys=True)
    return (f"{MARKER.format(sha=review['sha'])}\n{packet_md.rstrip()}\n\n"
            f"<details><summary>Machine-readable evidence for the gate</summary>\n\n```json\n{payload}\n```\n</details>\n")


def parse(body: str | None) -> tuple[str, dict] | None:
    """-> (sha from the marker, {"risk": ..., "review": ...}) or None when the body is not evidence."""
    marker = MARKER_RE.search(body or "")
    blocks = JSON_BLOCK_RE.findall(body or "")
    if not marker or not blocks:
        return None
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("risk"), dict) or not isinstance(data.get("review"), dict):
        return None
    return marker.group(1), data


def select(comments: list[dict], sha: str, humans: set[str]) -> dict | None:
    """The newest evidence comment for `sha` whose author is a human in `humans`, else None."""
    best: tuple[str, dict] | None = None
    for c in comments:
        user = c.get("user") or {}
        if user.get("type") == "Bot" or user.get("login", "").lower() not in humans:
            continue
        parsed = parse(c.get("body"))
        if not parsed or parsed[0] != sha:
            continue
        data = parsed[1]
        if data["review"].get("sha") != sha or data["risk"].get("sha") != sha:
            continue
        stamp = c.get("created_at", "")
        if best is None or stamp > best[0]:
            best = (stamp, data)
    return best[1] if best else None


def gh(path: str) -> dict:
    return json.loads(qlib.sh("gh", "api", path))


def gh_list(path: str) -> list:
    pages = json.loads(qlib.sh("gh", "api", "--paginate", "--slurp", path))
    return [item for page in pages for item in page]


def current_repo(explicit: str | None) -> str:
    return explicit or qlib.sh("gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()


def publish(args: argparse.Namespace) -> None:
    st = qlib.state_dir()
    sha = qlib.sh("git", "rev-parse", "HEAD").strip()
    paths = {"packet": st / "packet.md", "risk": st / "risk.json", "review": st / f"review-{sha[:12]}.json"}
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        sys.exit("evidence_comment: run review_loop.sh on this commit first; missing: " + ", ".join(missing))
    risk = json.loads(paths["risk"].read_text())
    review = json.loads(paths["review"].read_text())
    if review.get("sha") != sha or risk.get("sha") != sha:
        sys.exit(f"evidence_comment: the evidence is for another commit than HEAD {sha[:10]}; re-run the loop")
    repo = current_repo(args.repo)
    head = gh(f"repos/{repo}/pulls/{args.pr}")["head"]["sha"]
    if head != sha:
        sys.exit(f"evidence_comment: PR head is {head[:10]} but local HEAD is {sha[:10]}; push first")
    body = render(paths["packet"].read_text(), risk, review)
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(body)
    qlib.sh("gh", "pr", "comment", str(args.pr), "--repo", repo, "--body-file", fh.name)
    print(f"evidence for {sha[:10]} published to {repo}#{args.pr}")
    if args.approve:
        # Your sign-off on exactly this commit. The review event re-runs the gate by itself.
        qlib.sh("gh", "pr", "review", str(args.pr), "--repo", repo, "--comment", "--body", "/director-approve")
        print(f"/director-approve submitted on {sha[:10]}; the gate re-runs on the review event")
        return
    # The workflow has no comment trigger, so re-run this head's latest quality-gate job by hand;
    # it then reads the new comment.
    run = qlib.sh("gh", "run", "list", "--repo", repo, "--workflow", "quality-gate.yml", "--commit", sha,
                  "--event", "pull_request", "--limit", "1", "--json", "databaseId,status",
                  "-q", '.[0] | select(.status == "completed") | .databaseId', check=False).strip()
    job = run and qlib.sh("gh", "run", "view", run, "--repo", repo, "--json", "jobs",
                          "-q", '.jobs[] | select(.name == "quality-gate") | .databaseId', check=False).strip()
    if job:
        qlib.sh("gh", "run", "rerun", run, "--repo", repo, "--job", job, check=False)
        print(f"re-running the quality-gate job of run {run}")
    else:
        print("no finished quality-gate run for this head yet; it will read the comment when it runs")


def fetch(args: argparse.Namespace) -> None:
    cfg = qlib.load_config(args.config)
    humans = {h.lower() for h in cfg["risk"].get("humans", [])}
    if not humans:
        sys.exit("evidence_comment: [risk].humans is empty, so no evidence comment can be trusted")
    repo = current_repo(args.repo)
    comments = gh_list(f"repos/{repo}/issues/{args.pr}/comments?per_page=100")
    data = select(comments, args.sha, humans)
    if data is None:
        print(f"⏳ No evidence comment for {args.sha[:10]} by a human in [risk].humans yet. "
              "Run review_loop.sh locally on that commit, then evidence_comment.py publish --approve.")
        sys.exit(3)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    qlib.write_json(out / "risk.json", data["risk"])
    qlib.write_json(out / "review-head.json", data["review"])
    print(f"✅ evidence for {args.sha[:10]} taken from the PR comment")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--pr", required=True)
    p.add_argument("--approve", action="store_true", help="also submit your /director-approve review on the head commit")
    p.add_argument("--repo")
    f = sub.add_parser("fetch")
    f.add_argument("--pr", required=True)
    f.add_argument("--sha", required=True)
    f.add_argument("--repo")
    f.add_argument("--config")
    f.add_argument("--out", default=".quality")
    args = ap.parse_args()
    (publish if args.cmd == "publish" else fetch)(args)


if __name__ == "__main__":
    main()
