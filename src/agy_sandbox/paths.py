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


def plan_path(sandbox: str) -> Path:
    """The saved SandboxPlan a sandbox was created or last upgraded with."""
    return state_dir() / "sandboxes" / sandbox / "plan.json"


def workload_dir(sandbox: str) -> Path:
    """The generated v3 workload kit for a sandbox."""
    return state_dir() / "kits" / sandbox / "workload"


def recreate_staging(sandbox: str, stamp: str) -> Path:
    """Where kit volumes and the clone workspace wait while a sandbox is recreated."""
    return state_dir() / "recreate" / sandbox / stamp


def host_browser_dir(sandbox: str) -> Path:
    return state_dir() / "host-browser" / sandbox
