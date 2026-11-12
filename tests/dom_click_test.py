#!/usr/bin/env python3
"""End-to-end UI test for the v2 MyOS automation API."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.project_paths import REPO_ROOT
from mydomtester import MyOS, expect


def click_point(os, x, y, timeout: float = 2.0):
    """Click at (x, y) and wait until the DOM reacts; the guest runs only
    while a request is served, so a host-side sleep would let nothing happen."""
    before = os.snapshot().revision
    os._pointer_sequence([{"type": "move", "x": x, "y": y},
                          {"type": "down", "button": "left"},
                          {"type": "up", "button": "left"}])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and os.snapshot().revision == before:
        pass


def main() -> int:
    firmware = REPO_ROOT / "build" / "firmware_linked.mbin"
    disk = REPO_ROOT / "build" / "disk.img"
    if not firmware.exists() or not disk.exists():
        print("FAIL: build firmware and disk first")
        return 1
    try:
        with MyOS.launch(firmware, disk=disk, artifacts=REPO_ROOT / "qa" / "outputs" / "dom-click") as os:
            editor = os.get_by_role("window", name="Editor")
            menu_button = editor.get_by_role("menubutton", name="File")
            expect(menu_button).to_be_visible()
            expect(menu_button).to_have_count(1)

            # Boot's windows arrive in any order, and each lands on top.
            expect(os.get_by_role("window", name="Terminal")).to_be_visible()
            os.raise_window("Editor")

            # A menu button opens its menu; Esc closes it.
            menu_button.click()
            expect(os.get_by_role("menu")).to_be_visible()
            os._press("escape")
            expect(os.get_by_role("menu")).to_have_count(0)

            # Typing marks the text changed in the title.
            text = os.get_by_test_id("editor-text")
            text.click()
            os._type("Hello")
            expect(text).to_have_value("Hello")
            assert editor.snapshot().text == "*Untitled - Editor", editor.snapshot().text

            # A dialog's field takes fill; a click in it puts the caret where
            # it lands: just inside the left padding is before the first letter.
            os._press("s", 2)
            name = os.get_by_test_id("editor-name")
            expect(name).to_be_visible()
            name.fill("abc")
            expect(name).to_have_value("abc")
            box = name.snapshot().bounds
            click_point(os, box.x + 17, box.y + box.height // 2)
            os._type(">")
            expect(name).to_have_value(">abc")
            os._press("escape")
            expect(os.get_by_role("window", name="Save As")).to_have_count(0)
        print("PASS: strict locators, click, menu, fill and caret via automation bridge")
        return 0
    except (AssertionError, RuntimeError) as error:
        print(f"FAIL: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
