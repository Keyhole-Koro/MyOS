#!/usr/bin/env python3
"""End-to-end test for the MYOS-015 apps: a user process runs in the terminal,
the file manager lists the disk, and the editor round-trips a file.

Drives the real system through the v2 automation bridge (mydomtester), the
same path a person would use with mouse and keyboard.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.project_paths import REPO_ROOT  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mydomtester import MyOS, expect  # noqa: E402

ROW_H = 39  # menu row height at 2x UI scale (see dom.theme / text_height)


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
    time.sleep(0.35)


def launch_app(os, row_index):
    """Open the taskbar app menu and click the row'th item."""
    click_point(os, 40, 1516)  # the MyOS launcher at the taskbar's left
    menu = os.get_by_role("menu").snapshot()
    click_point(os, menu.bounds.x + 40,
                menu.bounds.y + 16 + ROW_H * row_index + ROW_H // 2)


def raise_window(os, name):
    """Bring a window to the front by clicking a clear spot on its title bar."""
    w = os.get_by_role("window", name=name).snapshot()
    click_point(os, w.bounds.x + w.bounds.width - 80, w.bounds.y + 16)


def main() -> int:
    firmware = REPO_ROOT / "build" / "firmware_linked.mbin"
    disk = REPO_ROOT / "build" / "disk.img"
    if not firmware.exists() or not disk.exists():
        print("FAIL: build firmware and disk first (make build)")
        return 1

    try:
        with MyOS.launch(firmware, disk=disk,
                         artifacts=REPO_ROOT / "qa" / "outputs" / "apps-e2e") as os:
            term_in = os.get_by_test_id("terminal-input")
            term_out = os.get_by_test_id("terminal-output")

            # 1. A user-space program runs as its own process; its stdout
            #    lands in the terminal, and its exit code is reported.
            expect(term_in).to_be_visible()
            term_in.click()
            os._type("hello")
            os._press("enter")
            wait_text_contains(term_out, "Hello from user space!")
            # The terminal notices the exit on its @timer, a tick or two later.
            wait_text_contains(term_out, "exited with 7")

            # `ls` is a terminal built-in reading the MFS disk.
            os._type("ls")
            os._press("enter")
            wait_text_contains(term_out, "readme.txt")

            # A typed Err crosses the syscall boundary; there is no negative
            # integer convention in the hosted FS API.
            os._type("cat definitely-missing")
            os._press("enter")
            wait_text_contains(term_out, "cat: no such file")
            os._type("ls definitely-missing")
            os._press("enter")
            wait_text_contains(term_out, "ls: no such directory")

            # 2. The editor saves a new file to the disk.
            launch_app(os, 1)  # Editor (menu is alphabetical: Counter, Editor, Files, Notes, Terminal)
            editor = os.get_by_role("window", name="Editor")
            expect(editor).to_be_visible()
            # The editor opens with "untitled.txt" in the name field; clear it
            # from the end (Locator.fill clears from Home, which a left-anchored
            # caret ignores) before typing the new name.
            name_field = os.get_by_test_id("editor-name")
            name_field.click()
            os._press("end")
            for _ in range(len("untitled.txt")):
                os._press("backspace")
            os._type("note.txt")
            os.get_by_test_id("editor-text").click()
            os._type("saved from the editor")
            os.get_by_role("button", name="Save").click()
            wait_text_contains(os.get_by_test_id("editor-status"), "saved")

            # 3. The file manager lists the disk, including the file the
            #    editor just wrote -- proof the save reached the disk image --
            #    and the directories the executables live in (MFS has
            #    directories: /bin for programs, /apps for desktop apps).
            launch_app(os, 2)  # Files
            files = os.get_by_role("window", name="Files")
            expect(files).to_be_visible()
            os.get_by_role("button", name="Refresh").click()
            listing = os.get_by_test_id("file-list").snapshot().text
            for name in ("bin/", "apps/", "readme.txt", "note.txt"):
                assert name in listing, f"{name} missing from the file list:\n{listing}"

            # Result<_, FsError> also crosses intact. Delete an existing file
            # and require the Ok arm to refresh the listing.
            note_row = next(i for i, row in enumerate(listing.split("\n"))
                            if row.startswith("note.txt"))
            list_box = os.get_by_test_id("file-list").snapshot().bounds
            click_point(os, list_box.x + 40, list_box.y + 20 + 39 * note_row)
            os.get_by_role("button", name="Delete").click()
            os.get_by_role("window", name="Delete file?").get_by_role("button", name="Delete").click()
            wait_text_contains(os.get_by_test_id("files-status"), "deleted")
            listing = os.get_by_test_id("file-list").snapshot().text
            assert not any(row.startswith("note.txt") for row in listing.split("\n")), \
                f"deleted file remains:\n{listing}"

            # 4. Entering a directory: activate "bin/" (double-click = click the
            #    selected row again) and the programs are listed; ".." leads back.
            rows = listing.split("\n")
            row = rows.index("bin/")
            box = os.get_by_test_id("file-list").snapshot().bounds
            for _ in range(2):
                click_point(os, box.x + 40, box.y + 20 + 39 * row)
            wait_text_contains(os.get_by_test_id("file-list"), "hello")
            listing = os.get_by_test_id("file-list").snapshot().text
            assert listing.split("\n")[0] == "..", f"no way back from /bin:\n{listing}"

        print("PASS: user process, editor disk round-trip and file manager via automation")
        return 0
    except (AssertionError, RuntimeError) as error:
        print(f"FAIL: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
