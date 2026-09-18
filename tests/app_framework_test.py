#!/usr/bin/env python3
"""End-to-end check of the application framework (system/MyOS/src/app).

Drives the built desktop through the automation bridge and pins what the
framework, not any one app, is responsible for:

  - the launcher menu lists every @app in the manifest and starts one;
  - @app(single) brings the running instance forward instead of a second one;
  - @key shortcuts reach the active window's app (Ctrl+S saves in the editor);
  - ui.open() routes a path to the @open app from another app (Files -> Editor);
  - closing a window frees the instance: a relaunch starts from initial state;
  - a dialog an app opened is a window of its own and closes with Cancel.

Run after `make build`:  python3 system/MyOS/tests/app_framework_test.py
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "system" / "MyOS" / "tests"))

from mydomtester import MyOS, expect  # noqa: E402

# Alphabetical, as gen_app_manifest.py orders the menu.
MENU = ["Counter", "Editor", "Files", "Notes", "Terminal"]
MOD_CTRL = 2
ROW_H = 39  # menu row height at 2x UI scale (see apps_e2e_test.py)


def wait_text_contains(locator, needle: str, timeout: float = 8.0) -> str:
    """Poll a node's text/value until it contains `needle`; return the text."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        node = locator.snapshot()
        last = node.value or node.text
        if needle in last:
            return last
        time.sleep(0.1)
    raise AssertionError(f"{needle!r} not found; last text was:\n{last}")


def click_point(os, x, y):
    os._pointer_sequence([{"type": "move", "x": x, "y": y},
                          {"type": "down", "button": "left"},
                          {"type": "up", "button": "left"}])


def launch_app(os, name):
    """Open the taskbar app menu and click the named row."""
    click_point(os, 40, 1516)  # the MyOS launcher at the taskbar's left
    menu = os.get_by_role("menu").snapshot()
    row = MENU.index(name)
    click_point(os, menu.bounds.x + 40, menu.bounds.y + 16 + ROW_H * row + ROW_H // 2)


def raise_window(os, name):
    w = os.get_by_role("window", name=name).snapshot()
    click_point(os, w.bounds.x + w.bounds.width - 120, w.bounds.y + 16)


def close_window(os, name):
    """The close button sits at the left of the title bar."""
    w = os.get_by_role("window", name=name).snapshot()
    click_point(os, w.bounds.x + 30, w.bounds.y + 16)


def main() -> int:
    firmware = REPO_ROOT / "build" / "firmware_linked.mbin"
    disk = REPO_ROOT / "build" / "disk.img"
    if not firmware.exists() or not disk.exists():
        print("FAIL: build firmware and disk first (make build)")
        return 1
    try:
        with MyOS.launch(firmware, disk=disk,
                         artifacts=REPO_ROOT / "qa" / "outputs" / "app-framework") as os:
            # 1. The launcher lists the manifest and starts an app.
            launch_app(os, "Editor")
            editor = os.get_by_role("window", name="Editor")
            expect(editor).to_be_visible()
            expect(os.get_by_test_id("editor-name")).to_have_value("untitled.txt")

            # 2. @app(single): a second launch is the same window.
            launch_app(os, "Editor")
            expect(os.get_by_role("window", name="Editor")).to_have_count(1)

            # 3. @key("Ctrl+S") on the editor saves (status label changes).
            #    The disk image keeps earlier runs' saves, so only the verb
            #    is checked, not the byte count.
            os.get_by_test_id("editor-text").click()
            os._type("via framework")
            os._press("s", MOD_CTRL)
            wait_text_contains(os.get_by_test_id("editor-status"), "saved ")

            # 4. ui.open() from Files reaches the editor's @open: "New" loads
            #    untitled.txt into the (still single) editor and raises it.
            launch_app(os, "Files")
            expect(os.get_by_role("window", name="Files")).to_be_visible()
            os.get_by_role("button", name="New").click()
            expect(os.get_by_role("window", name="Editor")).to_have_count(1)
            wait_text_contains(os.get_by_test_id("editor-status"), "loaded ")

            # 5. A dialog the app built is its own window; Cancel closes it.
            raise_window(os, "Files")
            listing = os.get_by_test_id("file-list").snapshot()
            click_point(os, listing.bounds.x + 40, listing.bounds.y + 20)  # first row
            wait_text_contains(os.get_by_test_id("files-status"), "selected ")
            os.get_by_role("button", name="Delete").click()
            expect(os.get_by_role("window", name="Delete file?")).to_be_visible()
            os.get_by_role("button", name="Cancel").click()
            expect(os.get_by_role("window", name="Delete file?")).to_have_count(0)

            # 6. Closing a window frees only that instance: Files goes, the
            #    Editor stays. Files covered the Counter, which is next.
            close_window(os, "Files")
            expect(os.get_by_role("window", name="Files")).to_have_count(0)
            expect(os.get_by_role("window", name="Editor")).to_have_count(1)

            # 7. A relaunch after close starts from the initial state.
            raise_window(os, "Counter")
            os.get_by_role("window", name="Counter").get_by_role("button", name="Click me").click()
            expect(os.get_by_test_id("counter")).to_have_text("clicks: 1")
            close_window(os, "Counter")
            expect(os.get_by_role("window", name="Counter")).to_have_count(0)
            launch_app(os, "Counter")
            expect(os.get_by_test_id("counter")).to_have_text("clicks: 0")

        print("PASS: launcher, single instance, @key, @open routing, close/relaunch, dialogs")
        return 0
    except (AssertionError, RuntimeError) as error:
        print(f"FAIL: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
