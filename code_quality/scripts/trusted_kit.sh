#!/usr/bin/env bash
# Stage the policy and scripts CI must trust: from the BASE branch, never from the PR.
#
#   trusted_kit.sh <base-sha> [<target-dir>]      (default target: _trusted)
#
# CI stages into a fresh directory OUTSIDE the PR checkout ($RUNNER_TEMP/trusted) and runs the
# scripts only from there. Python puts a script's own directory first on sys.path, so one file the
# PR left beside the trusted scripts (a `json.py`) would run inside the gate. The target must
# therefore not exist yet or be an empty real directory, and the staged tree may hold no symlinks
# (the kit ships none; a link could point back into the PR checkout).
#
# Result: <target>/code_quality, <target>/quality.toml (falling back to the kit's own copy
# when the base has no root policy) and <target>/REVIEW.md when the base has one.
#
# Bootstrap: the PR that installs the kit has a base with no kit, and a PR that upgrades the kit
# has a base whose scripts are older than the workflow it brings (code_quality/KIT_VERSION; a
# missing file is version 1). The old scripts lack the commands and flags the new workflow calls,
# so those PRs get their own copy staged instead, a BOOTSTRAP marker, and a visible warning. The
# marker is reported by ci_policy.py, and the quality-gate job then fails on purpose: a PR that
# changes the gate is merged deliberately, never by the gate. The marker also shows that the
# scripts (on install, also the policy) came from the PR. An upgrade keeps the base's quality.toml
# and REVIEW.md. All other PRs run entirely under the base copy. CI runs the BASE's copy of this
# script whenever the base has one (see ci/quality-gate.yml), so the PR cannot rewrite this decision.
set -euo pipefail

base="${1:?base sha}"
dst="${2:-_trusted}"
die() { echo "trusted_kit: $*" >&2; exit 1; }
[ ! -L "$dst" ] || die "$dst is a symlink; stage into a fresh directory outside the checkout"
if [ -e "$dst" ]; then
  [ -d "$dst" ] || die "$dst exists and is not a directory"
  [ -z "$(ls -A "$dst")" ] || die "$dst is not empty; stage into a fresh directory outside the checkout"
fi
mkdir -p "$dst"

# Missing file: version 1. Anything but a plain number: 999999, i.e. "newer than any base", which
# makes the PR a bootstrap PR, and the gate fails bootstrap PRs on purpose. A PR-controlled file
# can therefore only make its own PR fail, never pass.
kit_version() {
  local v; v="$(printf '%s' "$1" | tr -d '[:space:]')"
  if [ -z "$v" ]; then printf 1; elif [[ "$v" =~ ^[0-9]+$ ]]; then printf '%s' "$v"; else printf 999999; fi
}
base_version=0
if git cat-file -e "$base:code_quality/scripts/qlib.py" 2>/dev/null; then
  base_version="$(kit_version "$(git show "$base:code_quality/KIT_VERSION" 2>/dev/null || true)")"
fi
head_version="$(kit_version "$(cat code_quality/KIT_VERSION 2>/dev/null || true)")"

if [ "$base_version" -ge "$head_version" ]; then
  git archive "$base" code_quality | tar -x -C "$dst"
  for f in quality.toml REVIEW.md; do
    if git cat-file -e "$base:$f" 2>/dev/null; then
      git archive "$base" "$f" | tar -x -C "$dst"
    fi
  done
  echo "trusted_kit: policy and scripts staged from base ${base:0:12}"
else
  cp -R code_quality "$dst/"
  for f in quality.toml REVIEW.md; do
    if [ "$base_version" -gt 0 ]; then
      # Upgrade: only the scripts are new. The policy stays the base's, so a PR that bumps
      # KIT_VERSION cannot bring its own approval counts or floors along.
      if git cat-file -e "$base:$f" 2>/dev/null; then git archive "$base" "$f" | tar -x -C "$dst"; fi
    elif [ -f "$f" ]; then
      cp "$f" "$dst/"   # install: there is no base policy to keep
    fi
  done
  if [ "$base_version" -eq 0 ]; then
    reason="the base branch has no code_quality/ yet"
  else
    reason="the base branch's kit is version $base_version and this PR brings version $head_version"
  fi
  printf '%s\n' "$reason" > "$dst/BOOTSTRAP"
  echo "::warning title=quality bootstrap::$reason, so policy and scripts come from this PR. Read this PR as a change to the gate itself."
fi

links="$(find "$dst" -type l)"
[ -z "$links" ] || die "refusing symlinks in the staged kit: $links"

# The scripts read the root quality.toml; the kit's own copy is the documented fallback.
[ -f "$dst/quality.toml" ] || cp "$dst/code_quality/quality.toml" "$dst/quality.toml"
