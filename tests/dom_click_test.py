#!/usr/bin/env python3
"""Headless DOM-based click test for the MyKernel counter UI (MYOS-004).

Replaces the real-window/XTEST approach in gui_click_test.py: no X server,
no window geometry, no pixel cropping. Drives myemu --control-stdio and
asserts against the DOM/accessibility tree instead of screenshots.

Assumes system/MyKernel + firmware have already been built by
qa/runners/run_system.py --no-run into build/firmware_linked.mbin and
build/disk.img (with the kernel embedded -- see run_system.py for how the
disk image is laid out).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.project_paths import REPO_ROOT
from mydomtester import launch, expect


def display_dimension(function: str) -> int:
    config = REPO_ROOT / "system/MyOS/src/ui/display_config.mln"
    source = config.read_text(encoding="utf-8")
    marker = f"export i32 {function}()"
    body = source[source.index(marker):]
    return int(body.split("return", 1)[1].lstrip().split(";", 1)[0])


UI_SCALE = max(1, min(display_dimension("width") // 1024, display_dimension("height") // 768))


def main() -> int:
    build_dir = REPO_ROOT / "build"
    firmware = build_dir / "firmware_linked.mbin"
    disk = build_dir / "disk.img"
    if not firmware.exists() or not disk.exists():
        print(f"FAIL: missing {firmware} or {disk}; run qa/runners/run_system.py first")
        return 1

    page = launch(firmware, disk=disk)
    try:
        button = page.get_by_role("button", name="CLICK ME")
        expect(button).to_be_visible()

        # The counter is also the end-to-end Box/Column fixture. Snapshot
        # coordinates prove that Column replaced the children’s authored 0,0
        # positions and that its decorative containers do not capture input.
        nodes = page.dom_snapshot()
        box = next((n for n in nodes if n.get("kind") == 9), None)
        column = next((n for n in nodes if n.get("kind") == 13), None)
        button_node = button.resolve()
        label = next((n for n in nodes if n.get("text") == "clicks: 0"), None)

        assert box is not None and (
            box["x"], box["y"], box["w"], box["h"], box["visible"], box["hitTestable"]
        ) == (110 * UI_SCALE, 120 * UI_SCALE, 580 * UI_SCALE, 360 * UI_SCALE, True, False), box
        assert column is not None and (
            column["x"], column["y"], column["w"], column["h"], column["visible"], column["hitTestable"]
        ) == (120 * UI_SCALE, 130 * UI_SCALE, 540 * UI_SCALE, 320 * UI_SCALE, True, False), column
        assert button_node is not None and (button_node["x"], button_node["y"]) == (120 * UI_SCALE, 130 * UI_SCALE), button_node
        assert label is not None and (label["x"], label["y"]) == (120 * UI_SCALE, 206 * UI_SCALE), label

        button.click()
        expect(page.get_by_text("clicks: 1")).to_be_visible()

        button.click()
        button.click()
        expect(page.get_by_text("clicks: 3")).to_be_visible()

        print("PASS: clicks: 0 -> 1 -> 3 via headless DOM automation")
        return 0
    except AssertionError as e:
        print(f"FAIL: {e}")
        return 1
    finally:
        page.close()


if __name__ == "__main__":
    sys.exit(main())
