"""
Host-side state lives under one directory (default ~/.agy-sandbox). Every path
is derived here so tests redirect all of it with AGY_SANDBOX_STATE_DIR.
"""
import os
from pathlib import Path

from .constants import STATE_DIR

STATE_DIR_ENV = "AGY_SANDBOX_STATE_DIR"


def state_dir() -> Path:
    return Path(os.environ.get(STATE_DIR_ENV) or STATE_DIR).expanduser()


def kits_dir(sandbox: str) -> Path:
    """Everything generated for one sandbox's kits; removed with the sandbox."""
    return state_dir() / "kits" / sandbox


def workload_dir(sandbox: str) -> Path:
    """The generated v3 workload kit for a sandbox."""
    return kits_dir(sandbox) / "workload"


def snapshot_workload_dir(sandbox: str) -> Path:
    """
    The workload kit without its install hook, used to create a sandbox from a snapshot. Kept
    (not staged) because sbx records the kit path it created the sandbox with; the last
    directory is named like the normal kit so `sbx ls` shows the same agent label.
    """
    return kits_dir(sandbox) / "snapshot" / "workload"


def recreate_staging(sandbox: str, stamp: str) -> Path:
    """Where kit volumes and the clone workspace wait while a sandbox is recreated."""
    return state_dir() / "recreate" / sandbox / stamp


def host_browser_dir(sandbox: str) -> Path:
    return state_dir() / "host-browser" / sandbox
