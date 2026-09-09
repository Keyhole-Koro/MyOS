"""MYOS-012 phase 1: read-only DOM inspector CLI.

Dumps the live MyKernel DOM tree (via `Page.dom_snapshot()`, see
`mydomtester/__init__.py`) as an indented tree, optionally watching it for
changes. Does not write anything back to the kernel -- see MYOS-012 phase 2
for the planned prop-editing half.

    python3 -m system.MyOS.tests.mydomtester.inspect
    python3 -m system.MyOS.tests.mydomtester.inspect --watch
    python3 -m system.MyOS.tests.mydomtester.inspect --node 11
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.project_paths import REPO_ROOT  # noqa: E402

from . import MyDOMTesterError, launch  # noqa: E402

GREEN = "32"
YELLOW = "33"
RED = "31"
DIM = "2"

# Flags shown compactly in tree lines; order matches dom_click_test.py's
# reading of the same snapshot fields (see DOM_SPEC.md's state bitmask).
FLAG_ABBREV = [
    ("visible", "vis"),
    ("enabled", "en"),
    ("hovered", "hov"),
    ("pressed", "prs"),
    ("focused", "foc"),
    ("hitTestable", "hit"),
]


def _color(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _flags_str(node: dict) -> str:
    return " ".join(abbr for key, abbr in FLAG_ABBREV if node.get(key))


def _label(node: dict) -> str:
    name = node.get("name", "")
    text = node.get("text", "")
    if text and text != name:
        return f'"{name}" text="{text}"'
    if name:
        return f'"{name}"'
    return ""


def _node_line(node: dict, marker: str = " ") -> str:
    bounds = f"x={node['x']} y={node['y']} w={node['w']} h={node['h']}"
    flags = _flags_str(node)
    role = node.get("role", "none")
    parts = [f"#{node['id']}", f"role={role}", _label(node), bounds]
    if flags:
        parts.append(f"[{flags}]")
    return f"{marker} " + " ".join(p for p in parts if p)


def render_tree(nodes: list, changed: Optional[dict] = None) -> str:
    """changed maps id -> 'added'|'changed' for diff markup; ids absent from
    `nodes` but present as 'removed' in `changed` are listed separately."""
    changed = changed or {}
    by_id = {n["id"]: n for n in nodes}
    children: dict = {}
    for n in nodes:
        children.setdefault(n.get("parent", 0), []).append(n)

    lines = []

    def walk(node_id: int, depth: int) -> None:
        node = by_id[node_id]
        kind = changed.get(node_id)
        marker = {"added": "+", "changed": "*"}.get(kind, " ")
        line = ("  " * depth) + _node_line(node, marker)
        if kind == "added":
            line = _color(line, GREEN)
        elif kind == "changed":
            line = _color(line, YELLOW)
        lines.append(line)
        for child in children.get(node_id, []):
            walk(child["id"], depth + 1)

    roots = [n["id"] for n in nodes if n.get("parent", 0) not in by_id]
    for root_id in roots:
        walk(root_id, 0)

    removed = [v for v in changed.items() if v[1] == "removed"]
    for node_id, _ in removed:
        lines.append(_color(f"- removed #{node_id}", RED))

    return "\n".join(lines)


def diff_nodes(previous: dict, current: list) -> dict:
    """Return {id: 'added'|'changed'|'removed'} against a previous id->node map."""
    changed: dict = {}
    current_ids = set()
    for node in current:
        node_id = node["id"]
        current_ids.add(node_id)
        prev = previous.get(node_id)
        if prev is None:
            changed[node_id] = "added"
        elif prev != node:
            changed[node_id] = "changed"
    for node_id in previous:
        if node_id not in current_ids:
            changed[node_id] = "removed"
    return changed


def render_detail(node: dict) -> str:
    return "\n".join(f"{key}: {value!r}" for key, value in node.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "binary", nargs="?", default=str(REPO_ROOT / "build" / "firmware_linked.mbin"),
        help="Firmware .mbin to launch (default: build/firmware_linked.mbin)",
    )
    parser.add_argument(
        "--disk", default=str(REPO_ROOT / "build" / "disk.img"),
        help="Disk image to mount (default: build/disk.img)",
    )
    parser.add_argument("--watch", action="store_true", help="Poll and show diffs until Ctrl+C")
    parser.add_argument("--interval", type=float, default=0.5, help="Seconds between --watch ticks")
    parser.add_argument("--node", type=int, default=None, help="Show only this node id, in detail")
    args = parser.parse_args()

    if not Path(args.binary).exists():
        print(f"error: {args.binary} not found; build it first (make build)", file=sys.stderr)
        return 1

    try:
        page = launch(args.binary, disk=args.disk)
    except MyDOMTesterError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        previous: dict = {}
        while True:
            nodes = page.dom_snapshot()

            if args.node is not None:
                node = next((n for n in nodes if n["id"] == args.node), None)
                if node is None:
                    print(f"error: no node with id={args.node} in the current DOM", file=sys.stderr)
                    return 1
                print(render_detail(node))
            else:
                changed = diff_nodes(previous, nodes) if args.watch else {}
                print(render_tree(nodes, changed))

            previous = {n["id"]: n for n in nodes}

            if not args.watch:
                return 0
            print(_color(f"--- watching every {args.interval}s, Ctrl+C to stop ---", DIM))
            time.sleep(args.interval)
            page.frame_wait()
    except KeyboardInterrupt:
        return 0
    finally:
        page.close()


if __name__ == "__main__":
    sys.exit(main())
