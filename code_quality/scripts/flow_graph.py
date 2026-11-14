#!/usr/bin/env python3
"""Quick access to the quality flow graph (code_quality/flow.graph.json) for agents.

  flow_graph.py show [node]           # one node (default: entry), compact
  flow_graph.py next <node> <result>  # where to go, e.g. `next verify blockers` -> repair
  flow_graph.py path [from] [to]      # happy path between two nodes (default: entry -> done)
  flow_graph.py list                  # all nodes, one line each
  flow_graph.py mermaid               # regenerate a Mermaid diagram from the graph
  flow_graph.py validate              # every edge points to a node, every ref file exists

Add --json to show/next/list for machine output.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
GRAPH = json.loads((KIT / "flow.graph.json").read_text())
NODES: dict = GRAPH["nodes"]
HAPPY = {"done", "pass", "clean", "t1", "healthy"}  # preferred edge per node when tracing a path


def die(msg: str) -> None:
    sys.exit(f"flow_graph: {msg}")


def node(nid: str) -> dict:
    if nid not in NODES:
        die(f"unknown node '{nid}'. Nodes: {', '.join(NODES)}")
    return NODES[nid]


def fmt(nid: str) -> str:
    n = node(nid)
    lines = [f"[{nid}] {n['title']}  ({n['type']}, actor: {n.get('actor', '-')})"]
    for key in ("do", "command", "human", "enforced_by", "output"):
        if n.get(key):
            lines.append(f"  {key}: {n[key]}")
    if n.get("approvals"):
        lines.append("  approvals: " + ", ".join(f"{k}={v}" for k, v in n["approvals"].items()))
    for r in n.get("rules", []):
        lines.append(f"  rule: {r}")
    if n.get("next"):
        lines.append("  next: " + "  ".join(f"{k}->{v}" for k, v in n["next"].items()))
    if n.get("refs"):
        lines.append("  refs: " + ", ".join(n["refs"]))
    return "\n".join(lines)


def happy_path(src: str, dst: str) -> list[str]:
    """BFS that prefers happy edges, so the result is the normal route."""
    queue, seen = deque([[src]]), {src}
    while queue:
        path = queue.popleft()
        if path[-1] == dst:
            return path
        edges = node(path[-1]).get("next", {})
        for _, nxt in sorted(edges.items(), key=lambda kv: kv[0] not in HAPPY):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(path + [nxt])
    die(f"no path from {src} to {dst}")
    return []


def mermaid() -> str:
    shape = {"gate": ("{{", "}}"), "human_gate": ("[/", "/]"), "terminal": ("([", "])"), "step": ("[", "]")}
    out = ["flowchart TD"]
    for nid, n in NODES.items():
        a, b = shape.get(n["type"], ("[", "]"))
        label = n["title"].replace('"', "'")
        out.append(f'    {nid}{a}"{label}"{b}')
    for nid, n in NODES.items():
        for cond, nxt in n.get("next", {}).items():
            out.append(f"    {nid} -->|{cond}| {nxt}" if cond != "done" else f"    {nid} --> {nxt}")
    return "\n".join(out)


def validate() -> int:
    errors = []
    if GRAPH["entry"] not in NODES:
        errors.append(f"entry '{GRAPH['entry']}' is not a node")
    root = KIT.parent
    reachable = {GRAPH["entry"]}
    stack = [GRAPH["entry"]]
    while stack:
        for nxt in NODES.get(stack.pop(), {}).get("next", {}).values():
            if nxt not in reachable:
                reachable.add(nxt)
                stack.append(nxt)
    for nid, n in NODES.items():
        for cond, nxt in n.get("next", {}).items():
            if nxt not in NODES:
                errors.append(f"{nid} -[{cond}]-> {nxt}: unknown node")
        if n["type"] != "terminal" and not n.get("next"):
            errors.append(f"{nid}: non-terminal node without outgoing edges")
        if nid not in reachable:
            errors.append(f"{nid}: unreachable from entry")
        for ref in n.get("refs", []):
            if not (root / ref).exists():
                errors.append(f"{nid}: ref not found: {ref}")
    for inv in GRAPH.get("invariants", []):
        if not (root / inv["ref"]).exists():
            errors.append(f"invariant '{inv['rule']}': ref not found: {inv['ref']}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"{len(NODES)} nodes, {sum(len(n.get('next', {})) for n in NODES.values())} edges: "
          + ("OK" if not errors else f"{len(errors)} error(s)"))
    return 1 if errors else 0


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    cmd = argv[0] if argv else "show"
    if cmd == "show":
        nid = argv[1] if len(argv) > 1 else GRAPH["entry"]
        print(json.dumps({nid: node(nid)}, indent=2) if as_json else fmt(nid))
    elif cmd == "next":
        if len(argv) < 3:
            die("usage: next <node> <result>")
        edges = node(argv[1]).get("next", {})
        if argv[2] not in edges:
            die(f"'{argv[1]}' has no '{argv[2]}' edge. Options: {', '.join(edges) or '(terminal)'}")
        nxt = edges[argv[2]]
        print(json.dumps({"next": nxt, **node(nxt)}, indent=2) if as_json else fmt(nxt))
    elif cmd == "path":
        src = argv[1] if len(argv) > 1 else GRAPH["entry"]
        dst = argv[2] if len(argv) > 2 else "done"
        print(" -> ".join(happy_path(src, dst)))
    elif cmd == "list":
        if as_json:
            print(json.dumps({k: {"title": v["title"], "type": v["type"], "next": v.get("next", {})}
                              for k, v in NODES.items()}, indent=2))
        else:
            for k, v in NODES.items():
                edges = " ".join(f"{c}->{n}" for c, n in v.get("next", {}).items())
                print(f"{k:14} {v['type']:10} {edges}")
    elif cmd == "mermaid":
        print(mermaid())
    elif cmd == "validate":
        return validate()
    else:
        die(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
