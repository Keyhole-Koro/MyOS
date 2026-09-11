"""Print the live DOM exposed by the v2 automation bridge."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.project_paths import REPO_ROOT
from . import MyOS, Node, Snapshot


def render_tree(snapshot: Snapshot) -> str:
    by_parent: dict[int, list[Node]] = {}
    ids = {node.id for node in snapshot.nodes}
    for node in snapshot.nodes:
        by_parent.setdefault(node.parent, []).append(node)
    lines: list[str] = [f"revision={snapshot.revision}"]

    def visit(node: Node, depth: int) -> None:
        flags = " ".join(name for name, on in (("vis", node.visible), ("en", node.enabled),
                       ("focus", node.focused), ("checked", node.checked), ("hit", node.hit_testable)) if on)
        b = node.bounds
        label = node.text if node.text != node.name else node.name
        lines.append(f"{'  ' * depth}#{node.id} role={node.role} {label!r} "
                     f"x={b.x} y={b.y} w={b.width} h={b.height} [{flags}]")
        for child in by_parent.get(node.id, []): visit(child, depth + 1)

    for root in (node for node in snapshot.nodes if node.parent not in ids): visit(root, 0)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", nargs="?", default=str(REPO_ROOT / "build" / "firmware_linked.mbin"))
    parser.add_argument("--disk", default=str(REPO_ROOT / "build" / "disk.img"))
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()
    with MyOS.launch(args.binary, disk=args.disk) as os:
        previous = -1
        while True:
            snapshot = os.snapshot()
            if snapshot.revision != previous:
                print(render_tree(snapshot))
                previous = snapshot.revision
            if not args.watch: return 0
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
