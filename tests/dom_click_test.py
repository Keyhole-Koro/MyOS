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
        button = page.get_by_role("button", name="Click me")
        expect(button).to_be_visible()

        # The counter is also the end-to-end Panel/Column/Row fixture. Snapshot
        # coordinates prove that the window's children were placed relative to
        # its content area (below the 32px title bar), that Column and Row
        # replaced the children's authored 0,0 positions, and that decorative
        # containers do not capture input. Window: (96, 72); Panel at (20, 20)
        # inside it; Column at (20, 16) inside the panel.
        nodes = page.dom_snapshot()
        panel = next((n for n in nodes if n.get("kind") == 18), None)
        column = next((n for n in nodes if n.get("kind") == 13), None)
        row = next((n for n in nodes if n.get("kind") == 14), None)
        button_node = button.resolve()
        reset = page.get_by_role("button", name="Reset").resolve()
        label = next((n for n in nodes if n.get("text") == "clicks: 0"), None)

        title_bar = 32
        win_x, win_y = 96, 72 + title_bar
        assert panel is not None and (
            panel["x"], panel["y"], panel["w"], panel["h"], panel["visible"], panel["hitTestable"]
        ) == ((win_x + 20) * UI_SCALE, (win_y + 20) * UI_SCALE, 360 * UI_SCALE, 140 * UI_SCALE, True, False), panel
        assert column is not None and (
            column["x"], column["y"], column["visible"], column["hitTestable"]
        ) == ((win_x + 40) * UI_SCALE, (win_y + 36) * UI_SCALE, True, False), column
        assert label is not None and label["x"] == (win_x + 40) * UI_SCALE, label
        # Row lays the two buttons out left to right with a 10px gap.
        assert row is not None and button_node is not None and reset is not None
        assert (button_node["x"], button_node["y"]) == (row["x"], row["y"]), button_node
        assert reset["x"] == button_node["x"] + button_node["w"] + 10 * UI_SCALE, reset

        button.click()
        expect(page.get_by_text("clicks: 1")).to_be_visible()

        button.click()
        button.click()
        expect(page.get_by_text("clicks: 3")).to_be_visible()

        # "Count by two" doubles the step; Reset clears.
        page.get_by_role("checkbox", name="Count by two").click()
        button.click()
        expect(page.get_by_text("clicks: 5")).to_be_visible()
        page.get_by_role("button", name="Reset").click()
        expect(page.get_by_text("clicks: 0")).to_be_visible()

        # Keyboard: focus the Notes title field and type into it.
        page.get_by_role("textbox", name="Title").click()
        page.type_text("Hello")
        expect(page.get_by_text("You typed: Hello")).to_be_visible()
        page.key_press("backspace")
        expect(page.get_by_text("You typed: Hell")).to_be_visible()

        # Window management: drag the Counter window by its title bar, then
        # close the Notes window with its close button.
        counter = page.get_by_role("window", name="Counter")
        before = counter.resolve()
        counter.drag_by(60 * UI_SCALE, 40 * UI_SCALE)
        after = counter.resolve()
        assert (after["x"], after["y"]) == (before["x"] + 60 * UI_SCALE, before["y"] + 40 * UI_SCALE), after
        assert after["focused"], "dragging a window activates it"
        notes = page.get_by_role("window", name="Notes")
        notes.click_at(16 * UI_SCALE, 16 * UI_SCALE)
        expect(notes).to_be_gone()

        print("PASS: counter clicks, checkbox, keyboard, drag and close via headless DOM automation")
        return 0
    except AssertionError as e:
        print(f"FAIL: {e}")
        return 1
    finally:
        page.close()


if __name__ == "__main__":
    sys.exit(main())
