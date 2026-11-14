#!/usr/bin/env python3
"""Pin the deterministic risk tier of paths whose tier matters, under ./quality.toml.

A floor glob that grows an exclude (say `**/*.md`) can silently lower an operational file,
such as the root-run egress setup in scripts/egress/README.md, to Tier 0 with no human.
This check fails if any listed path no longer gets the tier it is pinned to here.
Exit 0 when all match, 1 otherwise.
"""

from __future__ import annotations

import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# path -> (tier, floor_hit). Every path is classified alone, as a one-line change.
EXPECTED = {
    # Operational docs and data: root-run setup steps, the pf hosts list.
    "scripts/egress/README.md": (2, True),
    "scripts/egress/vault.hosts": (2, True),
    "scripts/egress/hosts.txt": (2, True),
    "README.md": (2, True),
    # Product code and what it may ship: DocC catalogs, text resources.
    "Sources/VaultCore/VaultCore.docc/Overview.md": (2, True),
    "Sources/VaultCore/Resources/banks.txt": (2, True),
    "Package.swift": (2, True),
    # Policy, rules and license.
    "quality.toml": (2, True),
    "AGENTS.md": (2, True),
    "LICENSE.md": (2, True),
    # A path nobody listed yet.
    "tools/notes.md": (2, True),
    "tools/run.sh": (2, True),
    # The only unfloored paths: docs/ prose (deterministic Tier 0) and tests.
    "docs/architecture.md": (0, False),
    "docs/notes.txt": (0, False),
    "docs/build.sh": (2, True),
    "Tests/VaultCoreTests/LedgerTests.swift": (1, False),
}


def main() -> int:
    sys.path.insert(0, str(ROOT / "code_quality" / "scripts"))
    import classify_risk  # the kit's classifier, loaded from the vendored copy
    with open(ROOT / "quality.toml", "rb") as fh:
        cfg = tomllib.load(fh)
    bad = []
    for path, want in EXPECTED.items():
        tier, reasons, floor_hit = classify_risk.deterministic(cfg, [path], {path: (1, 0)})
        if (tier, floor_hit) != want:
            bad.append(f"{path}: tier {tier} floor_hit={floor_hit}, expected tier {want[0]} floor_hit={want[1]} ({'; '.join(reasons)})")
    for line in bad:
        print(f"check-risk-floors: {line}", file=sys.stderr)
    if not bad:
        print(f"check-risk-floors: {len(EXPECTED)} pinned paths at their expected tier")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
