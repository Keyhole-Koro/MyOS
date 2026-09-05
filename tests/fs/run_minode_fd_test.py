#!/usr/bin/env python3
"""
Minode & File Descriptor integration test runner for MyKernel / MyOS.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tools.project_paths import MYEMULATOR_DIR, MYOS_DIR, REPO_ROOT

BUILD_TOOLCHAIN = REPO_ROOT / "qa" / "runners" / "build_toolchain.py"
MYEMU = MYEMULATOR_DIR / "target" / "release" / "myemu"
SOURCE = MYOS_DIR / "tests" / "fs" / "test_minode_fd.mln"
STUB = MYOS_DIR / "tests" / "fs" / "test_minode_fd_stub.masm"

STEP_LIMIT = "50000000"
MARKER = "Minode and FD tests PASSED!"

GREEN, RED, CYAN = "32", "31", "36"

def colored(text, code):
    return f"\033[{code}m{text}\033[0m"

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="myos-minode-fd-test-"))
    linked = work / "test_minode_fd_linked.mbin"

    build = subprocess.run(
        ["python3", str(BUILD_TOOLCHAIN), str(STUB), str(SOURCE),
         "-o", str(linked), "--build-dir", str(work)],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=300,
    )
    if build.returncode != 0 or not linked.exists():
        print(colored("[FAIL]", RED), "minode/fd test build failed")
        if args.verbose:
            print(build.stdout)
        else:
            print(colored("[INFO]", CYAN), "re-run with --verbose to see build output")
        return 1

    disk_file = work / "disk.img"
    disk_file.touch()

    run = subprocess.run(
        [str(MYEMU), "-i", str(linked), "--step", STEP_LIMIT, "--headless", "--disk", str(disk_file)],
        cwd=work, capture_output=True, text=True, timeout=60,
    )
    out = run.stdout + run.stderr
    if args.verbose:
        print(out)

    if MARKER not in out:
        print(colored("[FAIL]", RED), "minode/fd test marker not seen")
        print(colored("[INFO]", CYAN), f"emulator tail: {out[-300:]!r}")
        if not args.verbose:
            print(colored("[INFO]", CYAN), "re-run with --verbose to see output")
        return 1

    print(colored("[PASS]", GREEN), "minode and FD integration test passed successfully!")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
