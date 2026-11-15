#!/usr/bin/env python3
"""Carry local review evidence to the PR, and back. For [review].where = "local".

  evidence_comment.py publish --pr N [--approve] [--repo owner/name]
      Posts .quality/packet.md plus the machine-readable risk.json and review-<head>.json as one
      PR comment. Run it after review_loop.sh converged, from the same checkout, with the head
      commit already pushed. --approve also submits your sign-off on that commit, so one command
      is the whole human step: an APPROVE review when you are not the PR author (a bot-opened
      PR), a `/director-approve` review when you are (GitHub won't let authors approve).
      Publication waits for the newest PR workflow run and re-runs its quality-gate job;
      --approve delegates that refresh to the review event (quality-gate-review.yml relays it to
      quality-gate-refresh.yml).

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
import time
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


def sign_off(me: str, pr: dict, cfg: dict) -> tuple[str, str, str | None]:
    """-> (review event, body, warning or None) for the human `me` signing off on `pr`.

    approval_gate.py counts two kinds of sign-off: an APPROVED review by anyone in [risk].humans,
    and, for the PR author only (GitHub won't let authors approve), a review whose body is the
    director marker. So a non-author approves; only the author uses the marker."""
    author = (pr.get("user") or {}).get("login", "")
    if me.lower() != author.lower():
        return "APPROVE", "Approved after reading the local review evidence for this commit.", None
    labels = {l["name"] for l in pr.get("labels", [])}
    humans = {h.lower() for h in cfg["risk"].get("humans", [])}
    missing = [why for ok, why in [
        (cfg["risk"].get("director_counts_as_reviewer"), "[risk].director_counts_as_reviewer is off"),
        ("agent-authored" in labels, "the PR lacks the agent-authored label"),
        (me.lower() in humans, f"{me} is not in [risk].humans")] if not ok]
    note = ("your director sign-off will not count: " + "; ".join(missing)) if missing else None
    return "COMMENT", "/director-approve", note


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
    pr = gh(f"repos/{repo}/pulls/{args.pr}")
    head = pr["head"]["sha"]
    if head != sha:
        sys.exit(f"evidence_comment: PR head is {head[:10]} but local HEAD is {sha[:10]}; push first")
    body = render(paths["packet"].read_text(), risk, review)
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(body)
    qlib.sh("gh", "pr", "comment", str(args.pr), "--repo", repo, "--body-file", fh.name)
    print(f"evidence for {sha[:10]} published to {repo}#{args.pr}")
    if args.approve:
        # Your sign-off on exactly this commit. The review event re-runs the gate by itself.
        # Pinned to the commit the evidence is for: if the head moved meanwhile, the sign-off stays on
        # that commit and does not count for the new one.
        me = qlib.sh("gh", "api", "user", "-q", ".login").strip()
        event, text, note = sign_off(me, pr, qlib.load_config())
        if note:
            print(f"warning: {note}", file=sys.stderr)
        qlib.sh("gh", "api", "-X", "POST", f"repos/{repo}/pulls/{args.pr}/reviews", "-f", f"commit_id={sha}",
                "-f", f"event={event}", "-f", f"body={text}")
        print(f"{event} review submitted on {sha[:10]}")
        print("Skipping local gate refresh: the review event triggers quality-gate-refresh.yml.")
    else:
        refresh_gate(repo, sha, pr)


def select_gate_run(runs: list[dict], sha: str, pr: dict) -> dict | None:
    """Newest pull_request run of `pr` for sha from the workflow-runs API, regardless of status/verdict.

    Callers list quality-gate.yml's runs. A run belongs to the PR when its pull_requests list names
    it, or (fork PRs, whose runs carry an empty list) when its exact PR title and head repository
    and branch match. A fork controls its title; two PRs can share the same head repository/branch.
    Creation time (then ID to break ties) determines recency; an older run's later re-run must not
    displace the newest PR run.
    """
    number = pr["number"]
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    head_ref = (pr.get("head") or {}).get("ref") or ""

    def ours(run: dict) -> bool:
        if any(p.get("number") == number for p in run.get("pull_requests") or []):
            return True
        return (not run.get("pull_requests") and head_repo not in (None, "")
                and run.get("display_title") == f"quality-gate for PR #{number}"
                and (run.get("head_repository") or {}).get("full_name") == head_repo
                and run.get("head_branch") == head_ref)

    return max((run for run in runs
                if run.get("event") == "pull_request" and run.get("head_sha") == sha and ours(run)),
               key=lambda run: (run["created_at"], run["id"]), default=None)


def refresh_gate(repo: str, sha: str, pr: dict) -> None:
    pages = json.loads(qlib.sh("gh", "api", "--paginate", "--slurp",
                              f"repos/{repo}/actions/workflows/quality-gate.yml/runs"
                              f"?event=pull_request&head_sha={sha}&per_page=100"))
    run = select_gate_run([run for page in pages for run in page["workflow_runs"]], sha, pr)
    if run is None:
        print(f"::notice title=No PR gate run yet::No quality-gate.yml pull_request run of #{pr['number']} "
              f"exists for {sha}; that run will read the evidence when it runs.")
        return
    run_id = str(run["id"])
    deadline = time.monotonic() + 20 * 60
    while time.monotonic() < deadline:
        current = gh(f"repos/{repo}/actions/runs/{run_id}")
        if current["status"] == "completed":
            break
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(15, remaining))
    else:
        sys.exit(f"::error title=Gate refresh timed out::quality-gate.yml run {run_id} did not complete "
                 "within 20 minutes; unable to re-evaluate quality-gate.")
    if current.get("conclusion") == "cancelled":
        # Cancelled runs were superseded (a newer push shares the cancel-in-progress group); that run
        # reads the evidence itself. Re-running this one would cancel the newer run in turn.
        print(f"::notice title=Gate run superseded::quality-gate.yml run {run_id} was cancelled; not re-running it.")
        return
    pages = json.loads(qlib.sh("gh", "api", "--paginate", "--slurp",
                              f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100"))
    job = next((job for page in pages for job in page["jobs"] if job["name"] == "quality-gate"), None)
    if job is None:
        sys.exit(f"evidence_comment: run {run_id} has no quality-gate job to re-run")
    # Last call before the re-run, to keep the window for a racing push as small as possible.
    live = gh(f"repos/{repo}/pulls/{pr['number']}")["head"]["sha"]
    if live != sha:
        print(f"::notice title=PR head moved::#{pr['number']} is now at {live[:10]}, not {sha[:10]}; "
              "the new head's own run reads the evidence. Not re-running the stale run.")
        return
    try:
        qlib.sh("gh", "run", "rerun", run_id, "--repo", repo, "--job", str(job["id"]))
    except SystemExit as exc:
        sys.exit(f"evidence_comment: GitHub refused to re-run quality-gate.yml run {run_id}; "
                 f"push a commit or close and reopen PR #{pr['number']} to start a fresh gate run.\n{exc}")
    print(f"re-running quality-gate job {job['id']} of run {run_id}")


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
    p.add_argument("--approve", action="store_true", help="also sign off on the head commit: APPROVE, or /director-approve if you authored the PR")
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
