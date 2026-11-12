#!/usr/bin/env python3
"""End-to-end test for the MYOS-015 apps: a user process runs in the terminal,
under /bin/sh, the file manager lists the disk, and the editor round-trips a
file.

Drives the real system through the v2 automation bridge (mydomtester), the
same path a person would use with mouse and keyboard.
"""
import re
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


def click_point(os, x, y, timeout: float = 2.0):
    """Click at (x, y) and wait until the DOM reacts.

    The emulator runs the guest only while it serves a control request, so a
    host-side sleep lets nothing happen; polling snapshots does."""
    before = os.snapshot().revision
    os._pointer_sequence([{"type": "move", "x": x, "y": y},
                          {"type": "down", "button": "left"},
                          {"type": "up", "button": "left"}])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and os.snapshot().revision == before:
        pass


def launch_app(os, name):
    """Open the taskbar app menu and click the named item: its rows are the
    menu's text, one per line."""
    click_point(os, 40, 1516)  # the MyOS launcher at the taskbar's left
    expect(os.get_by_role("menu")).to_be_visible()
    menu = os.get_by_role("menu").snapshot()
    row = menu.text.split("\n").index(name)
    click_point(os, menu.bounds.x + 40,
                menu.bounds.y + 16 + ROW_H * row + ROW_H // 2)


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
                         artifacts=REPO_ROOT / "qa" / "outputs" / "apps-e2e") as os:
            term = os.get_by_test_id("terminal")

            # 1. The terminal runs /bin/sh. A program the shell starts runs as
            #    its own process; its stdout lands in the terminal, and the
            #    shell reports its exit code once it ends.
            expect(term).to_be_visible()
            wait_text_contains(term, "$ ")
            # Boot's windows arrive in any order, and each lands on top.
            expect(os.get_by_role("window", name="Editor")).to_be_visible()
            os.raise_window("Terminal")
            term.click()
            os._type("hello")
            os._press("enter")
            wait_text_contains(term, "Hello from user space!")
            wait_text_contains(term, "exited with 7")

            # Threads and sync primitives in a real process, and a program
            # starting another one; each reports through its exit code.
            os._type("threads")
            os._press("enter")
            wait_text_contains(term, "exited with 42")
            os._type("spawner")
            os._press("enter")
            text = wait_text_contains(term, "exited with 47")
            # A reaped slot is the next one handed out, so each of these ran in
            # the same slot under a pid of its own: the low five bits are the
            # slot (tests/proc/pid_reuse.unsafe.test.mln).
            pids = re.findall(r"^\[pid (\d+) exited with \d+\]$", text, re.MULTILINE)
            assert len(pids) == 3 and len(set(pids)) == 3, f"pids: {pids}"
            assert len({int(pid) & 31 for pid in pids}) == 1, f"pids: {pids}"

            # A line typed while a program runs goes to the program, and the
            # next one to the shell again once it has ended.
            os._type("echo")
            os._press("enter")
            wait_text_contains(term, "echo: type lines")
            os._type("ls")
            os._press("enter")
            wait_text_contains(term, "> ls")
            os._type("quit")
            os._press("enter")
            wait_text_contains(term, "exited with 0]")

            # `ls` is a shell built-in reading the MFS disk.
            os._type("ls")
            os._press("enter")
            wait_text_contains(term, "readme.txt")

            # A typed Err crosses the syscall boundary; there is no negative
            # integer convention in the hosted FS API.
            os._type("cat definitely-missing")
            os._press("enter")
            wait_text_contains(term, "cat: no such file or directory")
            os._type("ls definitely-missing")
            os._press("enter")
            wait_text_contains(term, "ls: no such file or directory")

            # 2. The editor saves a new file to the disk: File > Save As asks
            #    for its name.
            launch_app(os, "Editor")
            editor = os.get_by_role("window", name="Editor")
            expect(editor).to_be_visible()
            os.get_by_test_id("editor-text").click()
            os._type("saved from the editor")
            menu = os.get_by_test_id("editor-file-menu").snapshot().bounds
            click_point(os, menu.x + menu.width // 2, menu.y + menu.height // 2)
            expect(os.get_by_role("menu")).to_be_visible()
            items = os.get_by_role("menu").snapshot()
            row = [line.split("\t")[0] for line in items.text.split("\n")].index("Save As...")
            click_point(os, items.bounds.x + 40, items.bounds.y + 16 + ROW_H * row + ROW_H // 2)
            expect(os.get_by_role("window", name="Save As")).to_be_visible()
            os.get_by_test_id("editor-name").fill("note.txt")
            os._press("enter")
            wait_text_contains(os.get_by_test_id("editor-status"), "saved")
            wait_text_contains(editor, "note.txt - Editor")

            # 3. The file manager lists the disk, including the file the
            #    editor just wrote -- proof the save reached the disk image --
            #    and the directories the executables live in (MFS has
            #    directories: /bin for programs, /apps for desktop apps).
            launch_app(os, "Files")
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

            # 5. Closing a terminal hangs up what runs in it: the OS ends the
            #    shell and the program it waits for, still waiting for input,
            #    and frees their pids, with no help from the app. Task Manager
            #    names the program's pid. The new terminal and its shell retake
            #    the old ones' pids, so the next program gets that one, the
            #    lowest number free.
            os.raise_window("Terminal")
            term.click()
            os._type("echo")
            os._press("enter")
            wait_text_contains(term, "echo: type lines")
            launch_app(os, "Task Manager")
            expect(os.get_by_role("window", name="Task Manager")).to_be_visible()
            procs = os.get_by_test_id("process-list")
            rows = wait_text_contains(procs, "\necho\t").split("\n")
            # Cells are apart by one or more tabs: name, pid, ...
            child = next(re.split(r"\t+", row)[1] for row in rows if row.startswith("echo\t"))
            close_window(os, "Task Manager")
            close_window(os, "Terminal")
            expect(os.get_by_role("window", name="Terminal")).to_have_count(0)
            launch_app(os, "Terminal")
            expect(term).to_be_visible()
            wait_text_contains(term, "$ ")
            term.click()
            os._type("hello")
            os._press("enter")
            text = wait_text_contains(term, "exited with 7")
            pids = re.findall(r"^\[pid (\d+) exited with 7\]$", text, re.MULTILINE)
            slots = [int(pid) & 31 for pid in pids]
            assert slots == [int(child) & 31], f"the closed terminal's program kept its slot: {child}, {pids}"

            # `exit` ends the shell, and with it the window.
            os._type("exit")
            os._press("enter")
            expect(os.get_by_role("window", name="Terminal")).to_have_count(0)
            launch_app(os, "Terminal")
            expect(term).to_be_visible()

            # 6. Task Manager reads every process from PROC_STATS: names from
            #    the paths they were started from, and a CPU sample a second.
            launch_app(os, "Task Manager")
            expect(os.get_by_role("window", name="Task Manager")).to_be_visible()
            procs = os.get_by_test_id("process-list")
            rows = wait_text_contains(procs, "\ntasks\t").split("\n")
            names = [row.split("\t")[0] for row in rows]
            for name in ("MyOS", "editor", "terminal", "sh", "tasks"):
                assert name in names, f"{name} missing from the process list:\n{rows}"
            wait_text_contains(os.get_by_test_id("tasks-summary"), "processes")
            graph = os.get_by_test_id("cpu-graph")
            deadline = time.monotonic() + 8.0
            while not graph.snapshot().text and time.monotonic() < deadline:
                pass
            assert graph.snapshot().text, "no CPU sample reached the graph"
            # Selecting a row draws its share over the graph.
            box = procs.snapshot().bounds
            click_point(os, box.x + 40, box.y + 20 + ROW_H * names.index("terminal"))
            wait_text_contains(os.get_by_test_id("selected-share"), "terminal")

        print("PASS: user process, editor disk round-trip, file manager, hangup and task manager via automation")
        return 0
    except (AssertionError, RuntimeError) as error:
        print(f"FAIL: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
