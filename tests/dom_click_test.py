#!/usr/bin/env python3
"""End-to-end UI test for the v2 MyOS automation API."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.project_paths import REPO_ROOT
from mydomtester import MyOS, expect


def wait_unchecked(locator, timeout: float = 5.0) -> None:
    """Poll until a checkbox is unchecked: expect() has no negative form."""
    deadline = time.monotonic() + timeout
    while locator.snapshot().checked:
        if time.monotonic() > deadline:
            raise AssertionError("checkbox stayed checked")


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
            notes = os.get_by_role("window", name="Notes")
            clear = notes.get_by_role("button", name="Clear")
            expect(clear).to_be_visible()
            expect(clear).to_be_enabled()
            expect(clear).to_have_count(1)

            # Boot's windows arrive in any order, and each lands on top.
            expect(os.get_by_role("window", name="Terminal")).to_be_visible()
            os.raise_window("Notes")

            # A checkbox toggles on click, with no handler behind it.
            done = notes.get_by_role("checkbox", name="Mark as done")
            expect(done).to_be_checked()
            done.click()
            wait_unchecked(done)
            done.click()
            expect(done).to_be_checked()

            title = os.get_by_role("textbox", name="Title")
            title.fill("Hello")
            expect(title).to_have_value("Hello")
            expect(os.get_by_text("You typed: Hello")).to_be_visible()

            # A click in a field puts the caret where it lands: just inside
            # the left padding is before the first letter.
            box = title.snapshot().bounds
            click_point(os, box.x + 17, box.y + box.height // 2)
            os._type(">")
            expect(title).to_have_value(">Hello")
            title.fill("Hello")

            # Clear writes to the fields and to the state the echo reads.
            os.get_by_role("button", name="Clear").click()
            expect(title).to_have_value("")
            expect(os.get_by_text("You typed: Hello")).to_have_count(0)
        print("PASS: strict locators, click, checkbox and fill via automation bridge")
        return 0
    except (AssertionError, RuntimeError) as error:
        print(f"FAIL: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
