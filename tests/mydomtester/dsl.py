"""MYOS-012 phase 3: a tiny line-based DSL for scripting DOM clicks.

Each line is one command, tokenized with shell-style quoting (so
`name="CLICK ME"` keeps the space). `#` starts a comment; blank lines are
ignored. This is a thin syntax over the existing Page/Locator API
(`mydomtester/__init__.py`) -- not a new protocol, no kernel/emulator changes.

Commands:
    click role=<role> name=<name>          click the first matching node
    click text=<text>
    click_at role=<role> name=<name> dx=<px> dy=<px>   click at an offset from the node's top-left
    drag role=<role> name=<name> dx=<px> dy=<px> [gx=<px> gy=<px>]   drag a node (default grab: title bar)
    type <text>                             type characters into the focused node
    key <name>                              press+release a key (enter, backspace, left, ...)
    wheel <steps>                           mouse wheel (positive = up)
    wait_for role=<role> name=<name> [timeout=<seconds>]   assert visible
    wait_for text=<text> [timeout=<seconds>]
    wait_gone role=<role> name=<name> [timeout=<seconds>]  assert not present
    wait_count role=<role> name=<name> count=<n>            assert number of matches
    (any locator also accepts nth=<index>; -1 = last match in tree order)
    dump                                    print the current DOM tree
    screenshot path=<path>                  save a screenshot (.png or .ppm)
    sleep <seconds>                         pause without touching the guest

Usage:
    python3 -m system.MyOS.tests.mydomtester.dsl script.domscript
    python3 -m system.MyOS.tests.mydomtester.dsl script.domscript build/firmware_linked.mbin --disk build/disk.img
"""

from __future__ import annotations

import argparse
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.project_paths import REPO_ROOT  # noqa: E402

from . import Locator, MyDOMTesterError, expect, launch  # noqa: E402
from .inspect import render_tree  # noqa: E402


class ScriptError(RuntimeError):
    """A .domscript line failed to parse or run."""


def _locator(page, kwargs: dict) -> Locator:
    role = kwargs.get("role")
    name = kwargs.get("name")
    text = kwargs.get("text")
    nth = int(kwargs.get("nth", 0))
    if role is None and text is None:
        raise ScriptError("needs role=... and/or text=... (or text=...) to find a node")
    return Locator(page, role=role, name=name, text=text, nth=nth)


def run_script(page, path: Path) -> None:
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        tokens = shlex.split(raw, comments=True)
        if not tokens:
            continue
        cmd, *rest = tokens
        kwargs: dict = {}
        positional: list = []
        for tok in rest:
            if "=" in tok:
                key, value = tok.split("=", 1)
                kwargs[key] = value
            else:
                positional.append(tok)

        try:
            if cmd == "click":
                _locator(page, kwargs).click()
            elif cmd == "click_at":
                dx = int(kwargs.pop("dx", 0))
                dy = int(kwargs.pop("dy", 0))
                _locator(page, kwargs).click_at(dx, dy)
            elif cmd == "drag":
                dx = int(kwargs.pop("dx", 0))
                dy = int(kwargs.pop("dy", 0))
                gx = kwargs.pop("gx", None)
                gy = kwargs.pop("gy", None)
                _locator(page, kwargs).drag_by(
                    dx, dy,
                    None if gx is None else int(gx),
                    None if gy is None else int(gy),
                )
            elif cmd == "type":
                page.type_text(" ".join(positional))
                page.frame_wait()
            elif cmd == "key":
                if not positional:
                    raise ScriptError("key needs a key name, e.g. `key enter`")
                page.key_press(positional[0])
                page.frame_wait()
            elif cmd == "wheel":
                if not positional:
                    raise ScriptError("wheel needs a step count, e.g. `wheel -1`")
                page.mouse_wheel(int(positional[0]))
                page.frame_wait()
            elif cmd == "wait_gone":
                timeout = float(kwargs.pop("timeout", 2.0))
                expect(_locator(page, kwargs)).to_be_gone(timeout=timeout)
            elif cmd == "wait_count":
                timeout = float(kwargs.pop("timeout", 2.0))
                count = int(kwargs.pop("count"))
                expect(_locator(page, kwargs)).to_have_count(count, timeout=timeout)
            elif cmd == "wait_for":
                timeout = float(kwargs.pop("timeout", 2.0))
                expect(_locator(page, kwargs)).to_be_visible(timeout=timeout)
            elif cmd == "dump":
                print(render_tree(page.dom_snapshot()))
            elif cmd == "screenshot":
                if "path" not in kwargs:
                    raise ScriptError("screenshot needs path=...")
                page.screenshot(kwargs["path"])
            elif cmd == "sleep":
                if not positional:
                    raise ScriptError("sleep needs a number of seconds, e.g. `sleep 0.5`")
                time.sleep(float(positional[0]))
            else:
                raise ScriptError(f"unknown command: {cmd}")
        except (ScriptError, MyDOMTesterError, AssertionError) as e:
            raise ScriptError(f"{path}:{lineno}: {raw.strip()!r}: {e}") from e


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("script", help="Path to a .domscript file")
    parser.add_argument(
        "binary", nargs="?", default=str(REPO_ROOT / "build" / "firmware_linked.mbin"),
        help="Firmware .mbin to launch (default: build/firmware_linked.mbin)",
    )
    parser.add_argument("--disk", default=str(REPO_ROOT / "build" / "disk.img"))
    args = parser.parse_args()

    script_path = Path(args.script)
    if not script_path.exists():
        print(f"error: script not found: {script_path}", file=sys.stderr)
        return 1
    if not Path(args.binary).exists():
        print(f"error: {args.binary} not found; build it first (make build)", file=sys.stderr)
        return 1

    try:
        page = launch(args.binary, disk=args.disk)
    except MyDOMTesterError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        run_script(page, script_path)
    except ScriptError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        page.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
