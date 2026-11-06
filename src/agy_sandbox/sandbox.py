"""
The only module that runs the `sbx` CLI. Functions return data or raise
SandboxError; they never print errors or exit. Every non-interactive call runs
with stdin closed so an sbx prompt can never hang the tool, and its stderr is
carried in the error so the user sees what sbx said.

JSON output is accepted in both shapes sbx has used: an object with a list under
a key (0.46: {"sandboxes": [...]}) and a bare array (0.48+).
"""
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Set, Tuple

from . import console
from .compose import SandboxPlan
from .constants import (
    DAEMON_PREFLIGHT_TIMEOUT,
    DEFAULT_CMD_TIMEOUT,
    LONG_TIMEOUT,
    REMOVE_CMD_TIMEOUT,
    SANDBOX_EXEC_TIMEOUT,
)
from .errors import SandboxError, SandboxUnavailable
from .kits import KitRef

NOT_INSTALLED = "Docker Sandboxes CLI ('sbx') is not installed or not on PATH."
NOT_INSTALLED_HINT = "Install it with 'brew install docker/tap/sbx' (macOS) or from https://github.com/docker/sbx-releases."
NOT_SIGNED_IN_MARKERS = ("401 Unauthorized", "sbx login")
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_STDERR_TAIL_LINES = 20


@dataclass(frozen=True, order=True)
class SbxVersion:
    major: int
    minor: int
    patch: int

    @property
    def as_tuple(self) -> Tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class SandboxInfo:
    name: str
    state: str
    kits: Tuple[str, ...]
    workspace: str
    clone: bool
    raw: dict = field(compare=False, repr=False, default_factory=dict)

    @property
    def running(self) -> bool:
        return self.state.lower() == "running"


# ---------------------------------------------------------------------------
# process plumbing
# ---------------------------------------------------------------------------
def _run(argv: Sequence[str], *, timeout: Optional[int]) -> subprocess.CompletedProcess:
    """A non-interactive sbx call: stdin closed, stdout and stderr captured."""
    cmd = ["sbx", *argv]
    try:
        res = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout
        )
    except FileNotFoundError:
        raise SandboxUnavailable(NOT_INSTALLED, hint=NOT_INSTALLED_HINT, command=cmd)
    except subprocess.TimeoutExpired:
        raise SandboxError(f"'{' '.join(cmd)}' did not finish within {timeout}s.", command=cmd, returncode=-1)
    _check_signed_in(res.stderr or "", cmd, res.returncode)
    return res


def _passthrough(argv: Sequence[str], *, what: str, timeout: Optional[int]) -> None:
    """A long sbx call whose output the user should watch (builds, snapshots): nothing captured."""
    cmd = ["sbx", *argv]
    console.command(cmd)
    try:
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, timeout=timeout)
    except FileNotFoundError:
        raise SandboxUnavailable(NOT_INSTALLED, hint=NOT_INSTALLED_HINT, command=cmd)
    except subprocess.TimeoutExpired:
        raise SandboxError(f"'sbx {what}' did not finish within {timeout}s.", command=cmd, returncode=-1)
    if res.returncode != 0:
        raise SandboxError(f"'sbx {what}' failed (exit {res.returncode}).", hint="See the sbx output above.",
                           command=cmd, returncode=res.returncode)


def _interactive(argv: Sequence[str]) -> int:
    """An sbx session attached to the terminal; returns its exit code."""
    cmd = ["sbx", *argv]
    console.command(cmd)
    try:
        return subprocess.run(cmd).returncode
    except FileNotFoundError:
        raise SandboxUnavailable(NOT_INSTALLED, hint=NOT_INSTALLED_HINT, command=cmd)


def _check_signed_in(stderr: str, cmd: Sequence[str], returncode: int) -> None:
    if any(marker in stderr for marker in NOT_SIGNED_IN_MARKERS):
        raise SandboxUnavailable(
            "The sbx CLI is not signed in to Docker.", hint="Run: sbx login",
            command=cmd, returncode=returncode, stderr=stderr,
        )


def _failure(what: str, res: subprocess.CompletedProcess, cmd: Sequence[str]) -> SandboxError:
    tail = "\n".join((res.stderr or "").strip().splitlines()[-_STDERR_TAIL_LINES:])
    return SandboxError(
        f"'sbx {what}' failed (exit {res.returncode}).", hint=tail or None,
        command=cmd, returncode=res.returncode, stderr=res.stderr or "",
    )


def _checked(argv: Sequence[str], *, what: str, timeout: Optional[int] = DEFAULT_CMD_TIMEOUT) -> subprocess.CompletedProcess:
    res = _run(argv, timeout=timeout)
    if res.returncode != 0:
        raise _failure(what, res, ["sbx", *argv])
    return res


def _parse_json(text: str, what: str) -> Any:
    """Parses the JSON document in sbx output, showing any sign-in text printed before it."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    start = min(starts) if starts else -1
    if start < 0:
        raise SandboxError(f"'sbx {what} --json' returned no JSON document.", hint=text.strip() or None)
    if text[:start].strip():
        console.info(text[:start].rstrip())
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError as e:
        raise SandboxError(f"'sbx {what} --json' returned something that is not JSON ({e}).")


def _json(argv: Sequence[str], *, timeout: Optional[int] = DEFAULT_CMD_TIMEOUT) -> Any:
    what = " ".join(argv)
    res = _checked([*argv, "--json"], what=what, timeout=timeout)
    return _parse_json(res.stdout or "", what)


def _items(doc: Any, key: str, what: str) -> List[dict]:
    """A list from either JSON shape: a bare array, or an object holding it under `key`."""
    if isinstance(doc, list):
        return [d for d in doc if isinstance(d, dict)]
    if isinstance(doc, dict):
        items = doc.get(key)
        return [d for d in (items or []) if isinstance(d, dict)]
    raise SandboxError(f"'sbx {what} --json' returned an unexpected document.")


def _creation_args(plan: SandboxPlan) -> List[str]:
    args: List[str] = []
    if plan.clone:
        args.append("--clone")
    for var in plan.env:
        args.extend(["-e", var])
    for port in plan.ports:
        args.extend(["--publish", port])
    for mixin in plan.mixins:
        args.extend(["--kit", mixin.source])
    for arg in plan.kit_args:
        args.extend(["--kit-arg", arg])
    return args


def _info_from(raw: dict) -> SandboxInfo:
    """Tolerates the 0.46 field names and the snake_case ones introduced in 0.48."""
    kits: List[str] = []
    for key in ("kits", "kit_refs", "mixins"):
        value = raw.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    item = item.get("ref") or item.get("source") or item.get("path") or ""
                if item:
                    kits.append(str(item))
            break
    return SandboxInfo(
        name=str(raw.get("name") or ""),
        state=str(raw.get("state") or raw.get("status") or ""),
        kits=tuple(kits),
        workspace=str(raw.get("workspace") or raw.get("workspace_path") or raw.get("workdir") or ""),
        clone=bool(raw.get("source_repo_dir") or raw.get("clone") or raw.get("cloned")),
        raw=raw,
    )


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------
def version() -> SbxVersion:
    res = _checked(["version"], what="version")
    match = _VERSION.search(res.stdout or "")
    if not match:
        raise SandboxUnavailable("Could not read the sbx version.", hint=(res.stdout or "").strip() or None)
    return SbxVersion(*(int(g) for g in match.groups()))


def require() -> SbxVersion:
    """The sbx binary is on PATH, its daemon answers and the CLI is signed in."""
    if not shutil.which("sbx"):
        raise SandboxUnavailable(NOT_INSTALLED, hint=NOT_INSTALLED_HINT)
    found = version()
    res = _run(["daemon", "status"], timeout=DAEMON_PREFLIGHT_TIMEOUT)
    if res.returncode != 0 or "running" not in (res.stdout or "").lower():
        raise SandboxUnavailable(
            "Docker Sandboxes daemon ('sandboxd') is not running.",
            hint="Start Docker Desktop, or run 'sbx daemon start'.",
            command=["sbx", "daemon", "status"], returncode=res.returncode, stderr=res.stderr or "",
        )
    return found


# ---------------------------------------------------------------------------
# sandboxes
# ---------------------------------------------------------------------------
def list_sandboxes() -> List[dict]:
    return _items(_json(["ls"]), "sandboxes", "ls")


def exists(name: str) -> bool:
    return any(s.get("name") == name for s in list_sandboxes())


def inspect(name: str) -> SandboxInfo:
    doc = _json(["inspect", name])
    if not isinstance(doc, dict):
        raise SandboxError(f"'sbx inspect {name} --json' returned an unexpected document.")
    return _info_from(doc)


def create(plan: SandboxPlan) -> None:
    """`sbx create`: the generated workload with every mixin attached and the workspace mounted. Does not attach."""
    argv = ["create", "--name", plan.name, *_creation_args(plan), plan.workload.dir, plan.workspace]
    _passthrough(argv, what="create", timeout=LONG_TIMEOUT)


def attach(name: str, env: Sequence[str] = ()) -> int:
    """Attaches to an existing sandbox, starting it if stopped; env applies to the session."""
    argv = ["run", "--name", name]
    for var in env:
        argv.extend(["-e", var])
    return _interactive(argv)


def stop(name: str) -> None:
    _checked(["stop", name], what=f"stop {name}", timeout=REMOVE_CMD_TIMEOUT)


def remove(name: str) -> None:
    """Removes a sandbox (stopping it first). Raises SandboxError if it is still there afterwards."""
    if not exists(name):
        return
    console.step(f"Removing sandbox '{name}'...")
    _run(["rm", "-f", name], timeout=REMOVE_CMD_TIMEOUT)
    if exists(name):
        raise SandboxError(f"Sandbox '{name}' could not be removed.", hint=f"Remove it manually: sbx rm -f {name}")


def publish_ports(name: str, ports: Sequence[str]) -> None:
    """Reconciles published ports on an existing sandbox; a failure is reported, not fatal."""
    if not ports:
        return
    argv = ["ports", name]
    for port in ports:
        argv.extend(["--publish", port])
    res = _run(argv, timeout=REMOVE_CMD_TIMEOUT)
    if res.returncode != 0:
        console.warn(f"'sbx ports' exited with code {res.returncode}; published ports may be out of date.")


def kit_add(name: str, kit: KitRef, kit_args: Sequence[str] = ()) -> None:
    """`sbx kit add`: attaches a mixin to an existing sandbox, keeping its contents."""
    argv = ["kit", "add", name, kit.source]
    for arg in kit_args:
        argv.extend(["--kit-arg", arg])
    _passthrough(argv, what="kit add", timeout=LONG_TIMEOUT)


def exec_script(name: str, script: str, user: Optional[str] = None) -> subprocess.CompletedProcess:
    """Runs a shell script inside a sandbox (starting it when stopped). Never raises on a non-zero exit."""
    argv = ["exec"] + (["-u", user] if user else []) + [name, "sh", "-c", script]
    return _run(argv, timeout=SANDBOX_EXEC_TIMEOUT)


# ---------------------------------------------------------------------------
# templates, files, secrets
# ---------------------------------------------------------------------------
def list_templates() -> List[dict]:
    return _items(_json(["template", "ls"]), "images", "template ls")


def save_template(name: str, tag: str) -> None:
    """Snapshots a (stopped) sandbox's whole filesystem as a template image."""
    _passthrough(["template", "save", name, tag], what="template save", timeout=LONG_TIMEOUT)


def remove_template(tag: str) -> None:
    _run(["template", "rm", "-f", tag], timeout=REMOVE_CMD_TIMEOUT)


def copy_from(name: str, path: str, dest_dir: str) -> None:
    """Copies a directory out of the sandbox; it lands as <dest_dir>/<basename of path>."""
    os.makedirs(dest_dir, exist_ok=True)
    _checked(["cp", f"{name}:{path}", dest_dir], what=f"cp {name}:{path}", timeout=LONG_TIMEOUT)


def copy_to(src_dir: str, name: str, dest_parent: str) -> None:
    """Copies a host directory into the sandbox under dest_parent, keeping its basename."""
    _checked(["cp", src_dir, f"{name}:{dest_parent}"], what=f"cp {src_dir} -> {name}:{dest_parent}", timeout=LONG_TIMEOUT)


def stored_secret_services(sandbox_name: str) -> Set[str]:
    """Service secrets visible to a sandbox: global ones plus those scoped to it."""
    services: Set[str] = set()
    for secret in _items(_json(["secret", "ls"]), "secrets", "secret ls"):
        if secret.get("type") != "service":
            continue
        scope = secret.get("scope") or "global"
        if scope in ("global", sandbox_name):
            services.add(str(secret.get("name")))
    return services
