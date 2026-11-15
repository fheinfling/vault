#!/usr/bin/env bash
# Deterministic quality gate. Runs the commands listed in quality.toml [checks].
#
#   quality_gate.sh fast          # lint/types: seconds. Used by the Stop hook.
#   quality_gate.sh full          # everything CI runs. Before review, in CI, in the merge queue.
#   quality_gate.sh fast --hook   # Stop-hook mode: exit 2 + reason on stderr so Claude keeps working.
#
# Skips (exit 0) in hook mode when the working tree hasn't changed since the last pass,
# so a chat turn with no edits costs nothing.
set -uo pipefail

MODE="${1:-full}"
HOOK="${2:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
STATE="$ROOT/.quality"; mkdir -p "$STATE"; [ -f "$STATE/.gitignore" ] || echo '*' > "$STATE/.gitignore"

fail_code=1; [ "$HOOK" = "--hook" ] && fail_code=2

fingerprint() { { git rev-parse HEAD 2>/dev/null; git diff HEAD 2>/dev/null; git ls-files --others --exclude-standard | xargs -I{} shasum {} 2>/dev/null; } | shasum | cut -c1-40; }

if [ "$HOOK" = "--hook" ]; then
  fp="$(fingerprint)"
  [ -f "$STATE/last-pass-$MODE" ] && [ "$(cat "$STATE/last-pass-$MODE")" = "$fp" ] && exit 0
  # No clean-tree shortcut: an agent that edits and commits within one turn leaves a clean tree,
  # and that first turn must still be checked. The fingerprint above (HEAD + diff + untracked)
  # makes every later no-op turn free.
fi

CMDS=()
while IFS= read -r line; do [ -n "$line" ] && CMDS+=("$line"); done < <(python3 -I - "$MODE" <<'PY'
import sys, tomllib, pathlib, subprocess
root = pathlib.Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
for p in (root / "quality.toml", root / "code_quality" / "quality.toml"):
    if p.is_file():
        print("\n".join(tomllib.loads(p.read_text()).get("checks", {}).get(sys.argv[1], [])))
        break
PY
)

if [ "${#CMDS[@]}" -eq 0 ] || [ -z "${CMDS[0]:-}" ]; then
  echo "quality_gate: no [checks].$MODE commands configured in quality.toml — nothing verified." >&2
  # An unconfigured gate is not a passing gate; fail loudly outside hook mode.
  [ "$HOOK" = "--hook" ] && exit 0 || exit 1
fi

failed=0
for cmd in "${CMDS[@]}"; do
  [ -z "$cmd" ] && continue
  log="$STATE/gate-$(echo "$cmd" | shasum | cut -c1-8).log"
  start=$(date +%s)
  if bash -c "$cmd" >"$log" 2>&1; then
    echo "PASS  ($(( $(date +%s) - start ))s)  $cmd"
  else
    failed=1
    echo "FAIL  ($(( $(date +%s) - start ))s)  $cmd"
    { echo "── $cmd failed. Last 60 lines:"; tail -n 60 "$log"; } >&2
  fi
done

if [ "$MODE" = "full" ]; then
  base="${QUALITY_BASE:-origin/main}"
  if git rev-parse --verify -q "$base" >/dev/null; then
    log="$STATE/gate-test-integrity.log"
    start=$(date +%s)
    if n=$({ python3 "$HERE/check_test_integrity.py" --base "$base" | python3 -I -c 'import json,sys; print(len(json.load(sys.stdin)["findings"]))'; } 2>"$log"); then
      [ "$n" -gt 0 ] && echo "NOTE  $n test-integrity signal(s) — the verifier will check whether they are justified (cross_review.py)."
    else
      failed=1
      echo "FAIL  ($(( $(date +%s) - start ))s)  check_test_integrity.py"
      { echo "── check_test_integrity.py failed. Last 60 lines:"; tail -n 60 "$log"; } >&2
    fi
  fi
fi

if [ "$failed" -ne 0 ]; then
  echo "Quality gate ($MODE) failed. Fix the errors above; do not weaken the checks." >&2
  exit "$fail_code"
fi
[ "$HOOK" = "--hook" ] && fingerprint > "$STATE/last-pass-$MODE"
echo "Quality gate ($MODE) passed."
