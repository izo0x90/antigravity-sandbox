"""
The error model. Library modules raise one of these and never print or exit;
`cli.main` is the single place that renders them and picks an exit code.
"""
from typing import List, Optional, Sequence


class AgyError(Exception):
    """A user-facing failure. `hint` is an optional one-line remedy."""

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return self.message


class ConfigError(AgyError):
    """agy.yaml or a saved sandbox plan is missing or invalid."""


class KitError(AgyError):
    """A kit reference could not be resolved or a descriptor could not be read."""


class SandboxError(AgyError):
    """A call to the sbx CLI failed."""

    def __init__(
        self,
        message: str,
        hint: Optional[str] = None,
        command: Sequence[str] = (),
        returncode: int = 0,
        stderr: str = "",
    ):
        super().__init__(message, hint)
        self.command = tuple(command)
        self.returncode = returncode
        self.stderr = stderr


class SandboxUnavailable(SandboxError):
    """sbx is not installed, its daemon is not running, or the CLI is not signed in."""


class UpgradeRefused(AgyError):
    """agy.yaml asks for a change sbx cannot apply to an existing sandbox in place."""

    def __init__(self, reasons: Sequence[str], hint: Optional[str] = None):
        self.reasons: List[str] = list(reasons)
        message = "This change cannot be applied in place:\n" + "\n".join(f"   - {r}" for r in self.reasons)
        super().__init__(message, hint)


class HostBrowserError(AgyError):
    """The host Chrome could not be found, started or reached."""
