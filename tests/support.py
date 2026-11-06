"""
Shared test fixtures. Tests assert on recorded sbx argv, returned dataclasses
and files written under an isolated state directory; never on printed text
except through `captured_console`.
"""
import contextlib
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple
from unittest.mock import patch

from agy_sandbox import console
from agy_sandbox.paths import STATE_DIR_ENV


class FakeSbx:
    """
    Stands in for subprocess.run. Every call is recorded; the answer comes from a
    table keyed by an argv prefix (longest match wins), defaulting to success.
    """

    def __init__(self) -> None:
        self.calls: List[List[str]] = []
        self.kwargs: List[dict] = []
        self._responses: Dict[Tuple[str, ...], object] = {}

    def respond(self, *prefix: str, stdout: str = "", returncode: int = 0, stderr: str = "") -> "FakeSbx":
        self._responses[tuple(prefix)] = subprocess.CompletedProcess(
            args=list(prefix), returncode=returncode, stdout=stdout, stderr=stderr
        )
        return self

    def fail(self, *prefix: str, returncode: int = 1, stderr: str = "boom") -> "FakeSbx":
        return self.respond(*prefix, returncode=returncode, stderr=stderr)

    def raise_on(self, *prefix: str, exc: Exception) -> "FakeSbx":
        self._responses[tuple(prefix)] = exc
        return self

    def __call__(self, argv: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
        argv = list(argv)
        self.calls.append(argv)
        self.kwargs.append(kwargs)
        best: Optional[Tuple[str, ...]] = None
        for prefix in self._responses:
            if argv[: len(prefix)] == list(prefix) and (best is None or len(prefix) > len(best)):
                best = prefix
        if best is None:
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")
        answer = self._responses[best]
        if isinstance(answer, Exception):
            raise answer
        return subprocess.CompletedProcess(args=argv, returncode=answer.returncode, stdout=answer.stdout, stderr=answer.stderr)

    def argv_of(self, verb: str) -> List[List[str]]:
        """Recorded sbx invocations whose verb (second element) matches."""
        return [c for c in self.calls if len(c) > 1 and c[0] == "sbx" and c[1] == verb]

    def verbs(self) -> List[str]:
        return [c[1] for c in self.calls if len(c) > 1 and c[0] == "sbx"]


@contextlib.contextmanager
def isolated_state() -> Iterator[Path]:
    """A temporary state directory exported through AGY_SANDBOX_STATE_DIR."""
    with tempfile.TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {STATE_DIR_ENV: tmp}):
            yield Path(tmp)


@contextlib.contextmanager
def captured_console() -> Iterator[List[Tuple[str, str]]]:
    """Collects (level, message) for every console call instead of printing."""
    messages: List[Tuple[str, str]] = []

    def recorder(level):
        def record(message, *args, **kwargs):
            text = " ".join(message) if isinstance(message, (list, tuple)) else str(message)
            messages.append((level, text))
        return record

    with patch.object(console, "step", recorder("step")), patch.object(console, "info", recorder("info")), \
         patch.object(console, "warn", recorder("warn")), patch.object(console, "hint", recorder("hint")), \
         patch.object(console, "command", recorder("command")), patch.object(console, "banner", recorder("banner")):
        yield messages


def messages_containing(messages: Sequence[Tuple[str, str]], needle: str) -> List[str]:
    return [m for _, m in messages if needle in m]
