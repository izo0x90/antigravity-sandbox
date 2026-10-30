"""
A thin wrapper over the `sbx` CLI. Functions here return data or raise
SandboxError; they do not print banners or exit. Every non-interactive call
runs with stdin closed so an sbx update prompt can never hang the tool.
"""
import json
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Sequence, Set

from .constants import (
    DAEMON_PREFLIGHT_TIMEOUT,
    DEFAULT_CMD_TIMEOUT,
    REMOVE_CMD_TIMEOUT,
    SANDBOX_EXEC_TIMEOUT,
    SBX_AGENT,
)


class SandboxError(RuntimeError):
    """sbx is unavailable or a call to it failed."""


def _sbx(args: Sequence[str], timeout: Optional[int] = DEFAULT_CMD_TIMEOUT, check: bool = False) -> subprocess.CompletedProcess:
    """Runs a non-interactive sbx command. stderr passes through so sign-in prompts stay visible."""
    cmd = ["sbx", *args]
    try:
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, timeout=timeout)
    except FileNotFoundError:
        raise SandboxError("Docker Sandboxes CLI ('sbx') is not installed or not on PATH.")
    except subprocess.TimeoutExpired:
        raise SandboxError(f"'{' '.join(cmd)}' did not finish within {timeout}s.")
    if check and res.returncode != 0:
        raise SandboxError(f"'{' '.join(cmd)}' failed with exit code {res.returncode}.")
    return res


def _sbx_json(args: Sequence[str], timeout: Optional[int] = DEFAULT_CMD_TIMEOUT) -> dict:
    res = _sbx([*args, "--json"], timeout=timeout, check=True)
    out = res.stdout or ""
    # A Docker sign-in flow can print before the document; show it and parse what follows.
    start = out.find("{")
    if start > 0 and out[:start].strip():
        print(out[:start].rstrip())
    try:
        return json.loads(out[start:] if start >= 0 else "{}")
    except json.JSONDecodeError:
        raise SandboxError(f"'sbx {' '.join(args)} --json' returned something that is not JSON.")


def _interactive(args: Sequence[str]) -> int:
    """Runs an sbx command attached to the terminal and returns its exit code."""
    cmd = ["sbx", *args]
    print(f"Executing: {' '.join(cmd)}")
    return subprocess.run(cmd).returncode


def require_sbx() -> None:
    """Preflight: the sbx binary is on PATH and sandboxd answers."""
    if not shutil.which("sbx"):
        raise SandboxError(
            "Docker Sandboxes CLI ('sbx') is not installed or not on PATH.\n"
            "Install Docker Desktop with Sandboxes support, or the standalone sbx CLI."
        )
    res = _sbx(["daemon", "status"], timeout=DAEMON_PREFLIGHT_TIMEOUT)
    if res.returncode != 0 or "running" not in (res.stdout or "").lower():
        raise SandboxError(
            "Docker Sandboxes daemon ('sandboxd') is not running.\n"
            "Start Docker Desktop, or run 'sbx daemon start'."
        )


def list_sandboxes() -> List[dict]:
    return _sbx_json(["ls"]).get("sandboxes") or []


def sandbox_exists(name: str) -> bool:
    return any(s.get("name") == name for s in list_sandboxes())


def inspect_sandbox(name: str) -> dict:
    return _sbx_json(["inspect", name])


def sandbox_kit_names(name: str) -> List[str]:
    """Basenames of the kits a sandbox was created with (bundled kits are attached by directory path)."""
    return [os.path.basename(str(k).rstrip("/")) for k in inspect_sandbox(name).get("kits") or []]


def template_loaded(image_id: str) -> bool:
    """True when an image with this ID is already in the sbx template store."""
    if not image_id:
        return False
    short = image_id.replace("sha256:", "")[:12]
    for image in _sbx_json(["template", "ls"]).get("images") or []:
        if str(image.get("id", "")).startswith(short):
            return True
    return False


def load_template(image_name: str) -> None:
    """Exports a local Docker image and loads it into the sbx template store."""
    print(f"Loading host image '{image_name}:latest' into the Docker Sandboxes template store...")
    fd, tar_path = tempfile.mkstemp(suffix=".tar")
    os.close(fd)
    try:
        subprocess.run(["docker", "save", "-o", tar_path, f"{image_name}:latest"], check=True)
        res = subprocess.run(["sbx", "template", "load", tar_path], stdin=subprocess.DEVNULL)
        if res.returncode != 0:
            raise SandboxError(f"'sbx template load' failed with exit code {res.returncode}.")
    except subprocess.CalledProcessError as e:
        raise SandboxError(f"'docker save' failed: {e}")
    finally:
        try:
            os.unlink(tar_path)
        except OSError:
            pass


def create_and_attach(
    name: str,
    image_name: str,
    kits: Sequence[str],
    kit_args: Sequence[str] = (),
    env: Sequence[str] = (),
    ports: Sequence[str] = (),
    clone: bool = False,
) -> int:
    """`sbx run` for a new sandbox: a shell on the project image with every kit attached."""
    args = ["run", "--name", name, "--template", f"{image_name}:latest"]
    for kit in kits:
        args.extend(["--kit", kit])
    for arg in kit_args:
        args.extend(["--kit-arg", arg])
    for var in env:
        args.extend(["-e", var])
    for port in ports:
        args.extend(["--publish", port])
    if clone:
        args.append("--clone")
    args.append(SBX_AGENT)
    return _interactive(args)


def attach(name: str, env: Sequence[str] = ()) -> int:
    """Re-attaches to an existing sandbox, starting it if stopped. env applies to the session."""
    args = ["run", "--name", name]
    for var in env:
        args.extend(["-e", var])
    return _interactive(args)


def publish_ports(name: str, ports: Sequence[str]) -> None:
    if not ports:
        return
    args = ["ports", name]
    for port in ports:
        args.extend(["--publish", port])
    print(f"Ensuring published ports on sandbox '{name}': {', '.join(ports)}...")
    res = _sbx(args)
    if res.returncode != 0:
        print(f"⚠️  Warning: 'sbx ports' exited with code {res.returncode}; published ports may be out of date.")


def remove_sandbox(name: str) -> None:
    """Stops and removes a sandbox. Raises SandboxError if it is still there afterwards."""
    if not sandbox_exists(name):
        return
    print(f"Stopping sandbox '{name}'...")
    _sbx(["stop", name], timeout=REMOVE_CMD_TIMEOUT)
    print(f"Removing sandbox '{name}'...")
    res = _sbx(["rm", "-f", name], timeout=REMOVE_CMD_TIMEOUT)
    if res.returncode != 0:
        _sbx(["rm", name], timeout=REMOVE_CMD_TIMEOUT)
    if sandbox_exists(name):
        raise SandboxError(f"Failed to remove sandbox '{name}'. Remove it manually with 'sbx rm {name}'.")


def exec_script(name: str, script: str) -> subprocess.CompletedProcess:
    """Runs a shell script inside a sandbox (starting it when stopped) and returns the result."""
    cmd = ["sbx", "exec", name, "sh", "-c", script]
    try:
        return subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=SANDBOX_EXEC_TIMEOUT
        )
    except FileNotFoundError:
        raise SandboxError("Docker Sandboxes CLI ('sbx') is not installed or not on PATH.")
    except subprocess.TimeoutExpired:
        raise SandboxError(f"'sbx exec {name}' did not finish within {SANDBOX_EXEC_TIMEOUT}s.")


def stored_secret_services(sandbox_name: str) -> Set[str]:
    """Service secrets visible to a sandbox: global ones plus those scoped to it."""
    services: Set[str] = set()
    for secret in _sbx_json(["secret", "ls"]).get("secrets") or []:
        if secret.get("type") != "service":
            continue
        scope = secret.get("scope") or "global"
        if scope in ("global", sandbox_name):
            services.add(str(secret.get("name")))
    return services

