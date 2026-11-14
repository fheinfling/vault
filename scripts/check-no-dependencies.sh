#!/bin/sh
# Fails if Package.swift declares any package dependency. See CLAUDE.md: zero package dependencies, ever.
set -eu
cd "$(dirname "$0")/.."
count=$(swift package dump-package | jq '.dependencies | length')
if [ "$count" != "0" ]; then
  echo "Package.swift must have no dependencies, found $count" >&2
  exit 1
fi
echo "ok: no package dependencies"
