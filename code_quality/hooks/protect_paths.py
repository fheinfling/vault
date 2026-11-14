#!/usr/bin/env python3
"""PreToolUse hook: agents may not edit the quality system that judges them,
and may not approve or sign off on PRs.

- Edit/Write on CI workflows, hooks, agent rules, review rules, or the policy -> blocked.
- Bash that writes to those paths, approves a PR, or forges a director sign-off -> blocked.
Exit 2 = block; stderr goes back to Claude as the reason.
Set QUALITY_ALLOW_POLICY_EDITS=1 for a session where a human has asked the agent
to change the quality system (the PR is still Tier 2: two humans). Approvals are
never allowed.

This is a guardrail, not a sandbox: a determined process can still write files
another way. The real enforcement is CI running the policy and scripts from the
base branch, plus CODEOWNERS/required workflows for the quality-system paths.
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from qlib import matches  # noqa: E402

PROTECTED = [".github/workflows/**", ".github/CODEOWNERS", ".github/PULL_REQUEST_TEMPLATE.md",
             ".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE/**", "quality.toml", "REVIEW.md", "AGENTS.md",
             "CLAUDE.md", ".claude/settings.json", ".claude/hooks/**", ".claude/agents/**",
             ".codex/**", "code_quality/**"]
PROT = (r"(\.github/workflows|\.github/CODEOWNERS|\.github/(PULL_REQUEST_TEMPLATE|pull_request_template)|"
        r"quality\.toml|REVIEW\.md|AGENTS\.md|CLAUDE\.md|"
        r"\.claude/(settings\.json|hooks|agents)|\.codex/|code_quality/)")
# Writes INTO a protected path. A plain `2>&1` or `> out.txt` while running the gate is fine.
REDIRECT_TO_PROT = re.compile(r">>?\s*['\"]?[^\s'\"&|;]*" + PROT)
# A mutating command whose arguments (same pipeline segment) name a protected path.
MUTATE_TO_PROT = re.compile(r"\b(sed\s+-i|perl\s+-i|tee|mv|cp|rm|chmod|truncate|git\s+(checkout|restore|rm|mv))\b[^;&|\n]*" + PROT)
APPROVE_RE = re.compile(r"(gh\s+pr\s+review|/director-approve|pulls/\d+/reviews|--approve\b|gh\s+pr\s+merge)")
# Review evidence (local review mode) is published by a human, never by the agent it judges:
# no publish command, no hand-made comment carrying the evidence marker, no edits to the files
# the loop wrote. Otherwise an agent could post "0 findings" under its human's account.
EVIDENCE = [".quality/review-*.json", ".quality/risk.json", ".quality/packet.md", ".quality/review.md"]
PUBLISH_RE = re.compile(r"evidence_comment\.py\s+publish")
EVIDENCE_WRITE_RE = re.compile(r"(>>?\s*['\"]?[^\s'\"&|;]*|\b(sed\s+-i|perl\s+-i|tee|mv|cp|rm|truncate|jq)\b[^;&|\n]*)"
                               r"\.quality/(review[^\s'\"]*\.(json|md)|risk\.json|packet\.md)")
EVIDENCE_MSG = ("Blocked: review evidence is published by a human, not by the agent under review. "
                "Leave .quality/ as the loop wrote it, and tell the human to run "
                "`python3 code_quality/scripts/evidence_comment.py publish --pr <n>` themselves.")


def block(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


event = json.load(sys.stdin)
tool = event.get("tool_name", "")
inp = event.get("tool_input") or {}


def strings(value) -> list[str]:
    """Every string anywhere in the tool input: MCP `body` fields, MultiEdit `edits`, nested args."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


texts = strings(inp)
# The evidence marker itself, not the word: a grep for "quality-evidence" or the artifact name is fine.
MARKER_RE = re.compile(r"<!--\s*quality-evidence\s+sha=")
if any(MARKER_RE.search(t) for t in texts):
    policy_edit_of_kit = False
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and os.environ.get("QUALITY_ALLOW_POLICY_EDITS") == "1":
        _p = inp.get("file_path") or inp.get("notebook_path") or ""
        _r = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()
        _p = _p if os.path.isabs(_p) else os.path.join(_r, _p)
        try:
            policy_edit_of_kit = matches(os.path.relpath(os.path.realpath(_p), os.path.realpath(_r)), PROTECTED)
        except ValueError:
            policy_edit_of_kit = False
    if not policy_edit_of_kit:  # a human-approved edit of the kit's own sources may contain the marker
        block(EVIDENCE_MSG)

# MCP tools (GitHub and others) act on the remote directly: no approving, merging or signing off
# through them either. Path rules don't apply; they have no local file argument.
MCP_APPROVE_TOOL = re.compile(r"(merge|approve|review)", re.I)
if tool.startswith("mcp__"):
    if MCP_APPROVE_TOOL.search(tool) or any(APPROVE_RE.search(t) or "/director-approve" in t for t in texts):
        block("Blocked: agents never approve, sign off on, or merge PRs, through any tool. "
              "Leave the PR for the human reviewer(s) the risk tier requires.")
    sys.exit(0)

if tool == "Bash":
    cmd = inp.get("command", "")
    if PUBLISH_RE.search(cmd) or EVIDENCE_WRITE_RE.search(cmd):
        block(EVIDENCE_MSG)
    if APPROVE_RE.search(cmd):
        block("Blocked: agents never approve, sign off on, or merge PRs. An agent's approval never counts "
              "as a person's. Leave the PR for the human reviewer(s) the risk tier requires.")
    if os.environ.get("QUALITY_ALLOW_POLICY_EDITS") != "1" and (
            REDIRECT_TO_PROT.search(cmd) or MUTATE_TO_PROT.search(cmd)):
        block("Blocked: this command appears to modify the quality system (rules, CI, hooks, policy). "
              "Agents don't edit the rules they are judged by. Ask the human if this really is the task.")
    sys.exit(0)

path = inp.get("file_path") or inp.get("notebook_path") or ""
_root = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()
try:
    _abs = path if os.path.isabs(path) else os.path.join(_root, path)
    _rel = os.path.relpath(os.path.realpath(_abs), os.path.realpath(_root)) if path else ""
except ValueError:
    _rel = ""
if _rel and not _rel.startswith("..") and matches(_rel, EVIDENCE):
    block(EVIDENCE_MSG)

if os.environ.get("QUALITY_ALLOW_POLICY_EDITS") == "1":
    sys.exit(0)

path = inp.get("file_path") or inp.get("notebook_path") or ""
root = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()
try:
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
except ValueError:
    sys.exit(0)
if not rel.startswith("..") and matches(rel, PROTECTED):
    block(f"Blocked: {rel} is part of the quality system (rules, CI, hooks, policy). "
          "Agents don't edit the rules they are judged by. If the task really is to change it, "
          "stop and ask the human to run the session with QUALITY_ALLOW_POLICY_EDITS=1.")
sys.exit(0)
