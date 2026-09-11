"""A strict, Playwright-inspired automation API for the MyOS UI tree.

The public surface intentionally stays small: ``MyOS``, ``Locator`` and
``expect``. It drives real emulator mouse/keyboard devices, while DOM reads
use the dedicated automation bridge rather than the interactive OS shell.
"""
from __future__ import annotations

import json
import select
import subprocess
import sys
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.project_paths import MYEMULATOR_DIR  # noqa: E402

DEFAULT_MYEMU = MYEMULATOR_DIR / "target" / "release" / "myemu"
LAUNCH_TIMEOUT_S = 15.0
COMMAND_TIMEOUT_S = 5.0
POLL_INTERVAL_S = 0.04


class MyOSError(RuntimeError):
    pass


class StrictModeError(MyOSError):
    pass


@dataclass(frozen=True)
class Bounds:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Node:
    id: int
    parent: int
    role: str
    name: str
    text: str
    test_id: str
    value: str
    bounds: Bounds
    visible: bool
    enabled: bool
    focused: bool
    checked: bool
    hit_testable: bool

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "Node":
        bounds = value["bounds"]
        return cls(
            id=value["id"], parent=value["parent"], role=value["role"],
            name=value["name"], text=value["text"], test_id=value["testId"],
            value=value["value"],
            bounds=Bounds(bounds["x"], bounds["y"], bounds["width"], bounds["height"]),
            visible=value["visible"], enabled=value["enabled"], focused=value["focused"],
            checked=value["checked"], hit_testable=value["hitTestable"],
        )


@dataclass(frozen=True)
class Snapshot:
    revision: int
    nodes: tuple[Node, ...]


class MyOS:
    """One running MyOS instance and its UI automation connection."""

    def __init__(self, proc: subprocess.Popen[str], artifacts: Optional[Path] = None):
        self._proc = proc
        self._next_id = 1
        self._log: list[str] = []
        self._actions: list[dict[str, Any]] = []
        self._artifacts = artifacts
        self._stderr_thread = threading.Thread(target=self._drain_guest_log, daemon=True)
        self._stderr_thread.start()

    def _drain_guest_log(self) -> None:
        if self._proc.stderr is None:
            return
        for line in self._proc.stderr:
            self._log.append(line)

    @classmethod
    def launch(
        cls, binary_path: str | Path, *, disk: str | Path | None = None,
        myemu_path: str | Path | None = None, artifacts: str | Path | None = None,
        extra_args: Optional[list[str]] = None,
    ) -> "MyOS":
        myemu = Path(myemu_path) if myemu_path else DEFAULT_MYEMU
        if not myemu.exists():
            raise MyOSError(f"myemu not found at {myemu}; build runtime/MyEmulator first")
        cmd = [str(myemu), "-i", str(binary_path), "--control-stdio"]
        if disk is not None:
            cmd += ["--disk", str(disk)]
        if extra_args:
            cmd += extra_args
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1)
        os = cls(proc, Path(artifacts) if artifacts else None)
        try:
            hello = os._request("session.hello", {}, timeout=LAUNCH_TIMEOUT_S)
            if hello.get("protocol") != 2:
                raise MyOSError(f"unsupported automation protocol: {hello!r}")
            os._request("os.ready", {}, timeout=LAUNCH_TIMEOUT_S)
            return os
        except BaseException:
            os.close()
            raise

    def _request(self, method: str, params: dict[str, Any], *, timeout: float = COMMAND_TIMEOUT_S) -> Any:
        if self._proc.poll() is not None:
            raise MyOSError(f"myemu exited with {self._proc.returncode}; guest log:\n" + "".join(self._log[-40:]))
        request_id = self._next_id
        self._next_id += 1
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
        self._proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MyOSError(f"timed out waiting for {method}; guest log:\n" + "".join(self._log[-40:]))
            ready, _, _ = select.select([self._proc.stdout], [], [], remaining)
            if not ready:
                continue
            line = self._proc.stdout.readline()
            if line == "":
                raise MyOSError(f"myemu closed protocol stdout during {method}")
            try:
                response = json.loads(line)
            except json.JSONDecodeError as error:
                raise MyOSError(f"non-JSON control response: {line!r}") from error
            if response.get("id") != request_id:
                raise MyOSError(f"out-of-order control response: {response!r}")
            if not response.get("ok"):
                error = response.get("error", {})
                raise MyOSError(f"{method}: {error.get('code')}: {error.get('message')}; {error.get('details')}")
            return response.get("result")

    def snapshot(self) -> Snapshot:
        raw = self._request("dom.snapshot", {})
        return Snapshot(raw["revision"], tuple(Node.from_json(node) for node in raw["nodes"]))

    def get_by_role(self, role: str, *, name: Optional[str] = None) -> "Locator":
        return Locator(self, role=role, name=name)

    def get_by_text(self, text: str) -> "Locator":
        return Locator(self, text=text)

    def get_by_test_id(self, test_id: str) -> "Locator":
        return Locator(self, test_id=test_id)

    def screenshot(self, path: str | Path) -> Path:
        result = self._request("screen.screenshot", {"path": str(path)})
        return Path(result["path"])

    def _pointer_sequence(self, events: list[dict[str, Any]]) -> None:
        self._request("input.pointer.sequence", {"events": events})

    def _hit_test(self, x: int, y: int) -> int:
        return int(self._request("dom.hit_test", {"x": x, "y": y})["id"])

    def _type(self, text: str) -> None:
        self._request("input.key.type", {"text": text})

    def _press(self, key: str, mods: int = 0) -> None:
        self._request("input.key.press", {"key": key, "mods": mods})

    def _record(self, action: str, locator: "Locator", node: Node, before: int, after: int, **extra: Any) -> None:
        self._actions.append({"action": action, "locator": repr(locator), "nodeId": node.id,
                              "revisionBefore": before, "revisionAfter": after, **extra})

    def _write_failure_artifacts(self) -> None:
        if self._artifacts is None:
            return
        self._artifacts.mkdir(parents=True, exist_ok=True)
        (self._artifacts / "actions.jsonl").write_text(
            "".join(json.dumps(action) + "\n" for action in self._actions), encoding="utf-8")
        try:
            raw = self._request("dom.snapshot", {})
            (self._artifacts / "dom.json").write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            self.screenshot(self._artifacts / "screenshot.png")
        except MyOSError:
            pass
        (self._artifacts / "guest.log").write_text("".join(self._log), encoding="utf-8")

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def __enter__(self) -> "MyOS":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is not None:
            self._write_failure_artifacts()
        self.close()


class Locator:
    def __init__(self, os: MyOS, *, role: str | None = None, name: str | None = None,
                 text: str | None = None, test_id: str | None = None,
                 parent: "Locator | None" = None, index: int | None = None):
        self._os, self._role, self._name = os, role, name
        self._text, self._test_id, self._parent, self._index = text, test_id, parent, index

    def __repr__(self) -> str:
        fields = [("role", self._role), ("name", self._name), ("text", self._text), ("test_id", self._test_id)]
        return "Locator(" + ", ".join(f"{key}={value!r}" for key, value in fields if value is not None) + ")"

    def get_by_role(self, role: str, *, name: str | None = None) -> "Locator":
        return Locator(self._os, role=role, name=name, parent=self)

    def get_by_text(self, text: str) -> "Locator":
        return Locator(self._os, text=text, parent=self)

    def get_by_test_id(self, test_id: str) -> "Locator":
        return Locator(self._os, test_id=test_id, parent=self)

    @property
    def first(self) -> "Locator": return self.nth(0)
    @property
    def last(self) -> "Locator": return self.nth(-1)
    def nth(self, index: int) -> "Locator":
        return Locator(self._os, role=self._role, name=self._name, text=self._text,
                       test_id=self._test_id, parent=self._parent, index=index)

    def _matches(self, node: Node) -> bool:
        return ((self._role is None or node.role == self._role)
                and (self._name is None or node.name == self._name)
                and (self._text is None or node.text == self._text)
                and (self._test_id is None or node.test_id == self._test_id))

    def _resolve_snapshot(self, snapshot: Snapshot) -> list[Node]:
        allowed: set[int] | None = None
        if self._parent is not None:
            parent_ids = {node.id for node in self._parent._resolve_snapshot(snapshot)}
            by_id = {node.id: node for node in snapshot.nodes}
            allowed = set()
            for node in snapshot.nodes:
                current = node.parent
                while current:
                    if current in parent_ids:
                        allowed.add(node.id)
                        break
                    ancestor = by_id.get(current)
                    current = ancestor.parent if ancestor else 0
        matches = [node for node in snapshot.nodes if self._matches(node) and (allowed is None or node.id in allowed)]
        if self._index is not None:
            try: return [matches[self._index]]
            except IndexError: return []
        return matches

    def resolve_all(self) -> list[Node]:
        return self._resolve_snapshot(self._os.snapshot())

    def count(self) -> int:
        return len(self.resolve_all())

    def snapshot(self) -> Node:
        nodes = self.resolve_all()
        if len(nodes) != 1:
            raise StrictModeError(f"{self!r}: expected exactly one node, found {len(nodes)}")
        return nodes[0]

    def _actionable(self, timeout: float) -> tuple[Snapshot, Node]:
        deadline = time.monotonic() + timeout
        previous: tuple[int, Bounds] | None = None
        last = "no matching node"
        while time.monotonic() < deadline:
            snapshot = self._os.snapshot()
            nodes = self._resolve_snapshot(snapshot)
            if len(nodes) != 1:
                last = f"expected exactly one node, found {len(nodes)}"
            else:
                node = nodes[0]
                bounds = node.bounds
                state = (node.visible and node.enabled and node.hit_testable
                         and bounds.width > 0 and bounds.height > 0)
                signature = (node.id, bounds)
                if not state:
                    last = f"node is not actionable: {node}"
                elif previous == signature:
                    x = bounds.x + bounds.width // 2
                    y = bounds.y + bounds.height // 2
                    hit = self._os._hit_test(x, y)
                    if hit == node.id:
                        return snapshot, node
                    last = f"node is covered at its click point by node #{hit}"
                else:
                    previous = signature
                    last = "waiting for stable bounds"
            time.sleep(POLL_INTERVAL_S)
        raise MyOSError(f"{self!r}: actionability timeout: {last}")

    def click(self, *, timeout: float = COMMAND_TIMEOUT_S) -> None:
        before, node = self._actionable(timeout)
        x = node.bounds.x + node.bounds.width // 2
        y = node.bounds.y + node.bounds.height // 2
        self._os._pointer_sequence([{"type": "move", "x": x, "y": y},
                                    {"type": "down", "button": "left"},
                                    {"type": "up", "button": "left"}])
        after = self._os.snapshot().revision
        self._os._record("click", self, node, before.revision, after, point={"x": x, "y": y})

    def focus(self, *, timeout: float = COMMAND_TIMEOUT_S) -> None:
        self.click(timeout=timeout)

    def fill(self, text: str, *, timeout: float = COMMAND_TIMEOUT_S) -> None:
        self.focus(timeout=timeout)
        self._os._press("home")
        # MyOS currently lacks selection/delete-all; clear with backspace.
        node = self.snapshot()
        for _ in node.value:
            self._os._press("backspace")
        self._os._type(text)

    def press(self, key: str) -> None:
        self._os._press(key)


class _Expect:
    def __init__(self, locator: Locator): self._locator = locator

    def _until(self, predicate: Any, description: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last: list[Node] = []
        while time.monotonic() < deadline:
            last = self._locator.resolve_all()
            if predicate(last): return
            time.sleep(POLL_INTERVAL_S)
        raise AssertionError(f"{self._locator!r}: expected {description}, found {last}")

    def to_be_visible(self, timeout: float = COMMAND_TIMEOUT_S) -> None:
        self._until(lambda ns: len(ns) == 1 and ns[0].visible, "one visible node", timeout)
    def to_be_hidden(self, timeout: float = COMMAND_TIMEOUT_S) -> None:
        self._until(lambda ns: not ns or not ns[0].visible, "hidden node", timeout)
    def to_be_enabled(self, timeout: float = COMMAND_TIMEOUT_S) -> None:
        self._until(lambda ns: len(ns) == 1 and ns[0].enabled, "one enabled node", timeout)
    def to_be_focused(self, timeout: float = COMMAND_TIMEOUT_S) -> None:
        self._until(lambda ns: len(ns) == 1 and ns[0].focused, "one focused node", timeout)
    def to_be_checked(self, timeout: float = COMMAND_TIMEOUT_S) -> None:
        self._until(lambda ns: len(ns) == 1 and ns[0].checked, "one checked node", timeout)
    def to_have_text(self, text: str, timeout: float = COMMAND_TIMEOUT_S) -> None:
        self._until(lambda ns: len(ns) == 1 and ns[0].text == text, f"text={text!r}", timeout)
    def to_have_value(self, value: str, timeout: float = COMMAND_TIMEOUT_S) -> None:
        self._until(lambda ns: len(ns) == 1 and ns[0].value == value, f"value={value!r}", timeout)
    def to_have_count(self, count: int, timeout: float = COMMAND_TIMEOUT_S) -> None:
        self._until(lambda ns: len(ns) == count, f"count={count}", timeout)


def expect(locator: Locator) -> _Expect:
    return _Expect(locator)
