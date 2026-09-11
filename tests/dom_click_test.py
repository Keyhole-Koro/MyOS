#!/usr/bin/env python3
"""End-to-end UI test for the v2 MyOS automation API."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.project_paths import REPO_ROOT
from mydomtester import MyOS, expect


def main() -> int:
    firmware = REPO_ROOT / "build" / "firmware_linked.mbin"
    disk = REPO_ROOT / "build" / "disk.img"
    if not firmware.exists() or not disk.exists():
        print("FAIL: build firmware and disk first")
        return 1
    try:
        with MyOS.launch(firmware, disk=disk, artifacts=REPO_ROOT / "qa" / "outputs" / "dom-click") as os:
            counter = os.get_by_role("window", name="Counter")
            increment = counter.get_by_role("button", name="Click me")
            expect(increment).to_be_visible()
            expect(increment).to_be_enabled()
            expect(increment).to_have_count(1)

            increment.click()
            expect(counter.get_by_test_id("counter")).to_have_text("clicks: 1")

            counter.get_by_role("checkbox", name="Count by two").click()
            increment.click()
            expect(counter.get_by_test_id("counter")).to_have_text("clicks: 3")

            title = os.get_by_role("textbox", name="Title")
            title.fill("Hello")
            expect(title).to_have_value("Hello")
            expect(os.get_by_text("You typed: Hello")).to_be_visible()
        print("PASS: strict locators, click, checkbox and fill via automation bridge")
        return 0
    except (AssertionError, RuntimeError) as error:
        print(f"FAIL: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
