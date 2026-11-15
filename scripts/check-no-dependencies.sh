#!/bin/sh
# Fails if Package.swift declares any package dependency. See CLAUDE.md: zero package dependencies, ever.
# The fuzzing package (Fuzz/) may depend only on this repository's root package, by local path.
set -eu
cd "$(dirname "$0")/.."
count=$(swift package dump-package | jq '.dependencies | length')
if [ "$count" != "0" ]; then
  echo "Package.swift must have no dependencies, found $count" >&2
  exit 1
fi
root=$(pwd -P)
fuzz=$(swift package --package-path Fuzz dump-package | jq -r --arg root "$root" '
  if (.dependencies | length) == 1 and (.dependencies[0] | keys) == ["fileSystem"]
     and .dependencies[0].fileSystem[0].path == $root then "ok" else "bad" end')
if [ "$fuzz" != "ok" ]; then
  echo "Fuzz/Package.swift may depend only on the root package by local path" >&2
  exit 1
fi
echo "ok: no package dependencies (Fuzz/ uses only the local root package)"
