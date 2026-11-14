#!/usr/bin/env bash
# Autonomous gate → cross-model review → repair loop. No human in the loop
# until it either converges (prints an evidence packet) or hits the round cap
# (prints an escalation packet). Run it on a feature branch with commits.
#
#   review_loop.sh [--base origin/main] [--intent "task / acceptance criteria"] [--depth deep]
#
# Exit: 0 converged · 1 gate never went green · 3 round cap hit (human needed) · 2 tool error
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT="$(dirname "$HERE")"
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
BASE="origin/main"; INTENT=""; DEPTH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --base) BASE="$2"; shift 2 ;;
    --intent) INTENT="$2"; shift 2 ;;
    --depth) DEPTH="--depth $2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
export QUALITY_BASE="$BASE"
# Review under the BASE branch's policy and rules, never the ones this change proposes. The gate
# checks the fingerprint (cross_review.py records it) against the base's copies.
if git cat-file -e "$BASE:quality.toml" 2>/dev/null; then
  mkdir -p "$ROOT/.quality/base-policy"
  git show "$BASE:quality.toml" > "$ROOT/.quality/base-policy/quality.toml"
  git show "$BASE:REVIEW.md" > "$ROOT/.quality/base-policy/REVIEW.md" 2>/dev/null \
    || git show "$BASE:code_quality/rules/REVIEW.md" > "$ROOT/.quality/base-policy/REVIEW.md" 2>/dev/null \
    || rm -f "$ROOT/.quality/base-policy/REVIEW.md"
  export QUALITY_POLICY="$ROOT/.quality/base-policy/quality.toml"
  [ -f "$ROOT/.quality/base-policy/REVIEW.md" ] && export QUALITY_REVIEW_MD="$ROOT/.quality/base-policy/REVIEW.md"
  echo "Reviewing under the base branch's policy ($BASE)."
fi
if [ -z "$DEPTH" ]; then  # Tier 2 is reviewed deep; approval_gate.py rejects anything less for it
  FLOOR_TIER="$(python3 "$HERE/classify_risk.py" --base "$BASE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tier"])')"
  [ "$FLOOR_TIER" = "2" ] && DEPTH="--depth deep" && echo "Tier 2 change: deep review (every deep_families member)."
fi
STATE="$ROOT/.quality"; mkdir -p "$STATE"; [ -f "$STATE/.gitignore" ] || echo '*' > "$STATE/.gitignore"

cfg() { python3 -c "import sys; sys.path.insert(0,'$HERE'); import qlib; c=qlib.load_config(); print($1)"; }
IMPL="$(cfg 'c["agents"]["implementer"]')"
MAX_ROUNDS="$(cfg 'c["review"].get("max_rounds",3)')"
DIFF_ONLY_AFTER="$(cfg 'c["review"].get("diff_only_after_round",2)')"
CLAUDE_MODEL="$(cfg 'c["agents"]["claude_model"]')"
CODEX_MODEL="$(cfg 'c["agents"]["codex_model"]')"
# Implementer permissions. Run the loop in a worktree/sandbox; override if your setup differs.
CLAUDE_IMPL_ARGS="${CLAUDE_IMPL_ARGS:---permission-mode auto}"

implement() {  # $1 = prompt
  if [ "$IMPL" = "codex" ]; then
    printf '%s' "$1" | codex exec -m "$CODEX_MODEL" -s workspace-write --ephemeral -
  else
    # prompt via stdin, like codex above: agents/fixer.md starts with "---" frontmatter, which the CLI would parse
    # as an option ("error: unknown option '---…'"), and the implementer would not run at all
    # shellcheck disable=SC2086
    printf '%s' "$1" | claude -p --model "$CLAUDE_MODEL" $CLAUDE_IMPL_ARGS
  fi
}

commit_if_dirty() {  # reviews read commits, so every repair must be committed
  if [ -n "$(git status --porcelain -- . ':!.quality')" ]; then
    # plain `git add -A`: .quality/ is git-ignored (this loop writes .quality/.gitignore), and naming an ignored path
    # in the pathspec (':!.quality') makes `git add` exit 1, which would skip the commit
    git add -A && git commit -q -m "$1" \
      || { echo "review_loop: could not commit the repair ($1): stopping, an uncommitted repair is never reviewed."; exit 2; }
  fi
}

gate_until_green() {  # up to 3 repair attempts for deterministic failures
  for attempt in 1 2 3; do
    if out="$("$HERE/quality_gate.sh" full 2>&1)"; then echo "$out"; return 0; fi
    echo "$out" | tail -n 80
    echo "── gate failed (attempt $attempt) — handing errors to $IMPL"
    implement "$(cat "$KIT/agents/fixer.md")

## Deterministic gate failures (fix these first)
\`\`\`
$(echo "$out" | tail -n 150)
\`\`\`" || { echo "Implementer failed to run (see its output above): stopping, a tool error is not a repair."; exit 2; }
    commit_if_dirty "review-loop: fix gate failures (attempt $attempt)"
  done
  "$HERE/quality_gate.sh" full
}

: > "$STATE/rounds.log"
rm -f "$STATE/ledger.json" "$STATE/responses.json"
last_reviewed=""
for round in $(seq 1 "$MAX_ROUNDS"); do
  echo "═══ Round $round/$MAX_ROUNDS"
  gate_until_green || { echo "Gate still red after repairs."; exit 1; }

  since=""
  if [ "$round" -ge "$DIFF_ONLY_AFTER" ] && [ -n "$last_reviewed" ]; then since="--since $last_reviewed"; fi
  head_sha="$(git rev-parse HEAD)"
  # shellcheck disable=SC2086
  python3 "$HERE/cross_review.py" --base "$BASE" $since $DEPTH --intent "$INTENT"
  rc=$?
  echo "round=$round sha=$head_sha rc=$rc" >> "$STATE/rounds.log"
  last_reviewed="$head_sha"

  if [ $rc -eq 0 ]; then
    python3 "$HERE/classify_risk.py" --base "$BASE" --model > /dev/null
    python3 "$HERE/evidence_packet.py" --base "$BASE" --rounds "$round" --status converged
    if [ "$(cfg 'c["review"].get("where","ci")')" = "local" ]; then
      echo "Local review mode: a HUMAN publishes the evidence, after this commit is pushed (agents are blocked from it):"
      echo "  python3 $HERE/evidence_comment.py publish --pr <number>"
    fi
    exit 0
  fi
  if [ $rc -ne 1 ]; then
    echo "Review incomplete (rc=$rc): a reviewer or verifier failed — see .quality/review.md. Not a code problem; fix the tooling and re-run."
    exit 2
  fi

  [ "$round" -eq "$MAX_ROUNDS" ] && break
  implement "$(cat "$KIT/agents/fixer.md")

## Verified findings (round $round)
$(cat "$STATE/review.md")" || { echo "Implementer failed to run (see its output above): stopping, a tool error is not a repair."; exit 2; }
  commit_if_dirty "review-loop: address round $round findings"
  if [ "$(git rev-parse HEAD)" = "$head_sha" ]; then
    echo "Implementer made no code changes; re-reviewing with its responses."
  fi
done

python3 "$HERE/classify_risk.py" --base "$BASE" --model > /dev/null
python3 "$HERE/evidence_packet.py" --base "$BASE" --rounds "$MAX_ROUNDS" --status escalated
exit 3
