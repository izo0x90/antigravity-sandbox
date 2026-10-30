"""
Kit resolution. A kit reference in agy.yaml is one of:
  - a bundled kit name (a directory under kits/), which is also how agents are named
  - a local directory holding spec.yaml / spec.yml
  - a remote reference (git+..., https://..., oci://...)

Login mode reaches kits through one mechanism: bundled kits that declare an
`auth_mode` argument receive it as `--kit-arg <kit>.auth_mode=<mode>`, and a
bundled `<kit>-proxy` kit (the static credential declaration sbx needs for
proxy injection) is attached only in proxy-managed mode.
"""
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import yaml

from .agents import AGENT_SPECS
from .constants import (
    AUTH_MODE_KIT_ARG,
    AUTH_MODE_PROXY_MANAGED,
    PROJECT_KIT_NAME,
    PROXY_KIT_SUFFIX,
    REMOTE_KIT_SCHEMES,
    STATE_DIR,
)

KITS_DIR = Path(__file__).resolve().parent / "kits"


class _KitDumper(yaml.SafeDumper):
    """Writes multi-line strings (hook scripts) as literal blocks so generated specs stay readable."""


def _str_presenter(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_KitDumper.add_representer(str, _str_presenter)


def _spec_path(kit_dir: Path) -> Optional[Path]:
    for name in ("spec.yaml", "spec.yml"):
        if (kit_dir / name).is_file():
            return kit_dir / name
    return None


def _load_spec(kit_dir: Path) -> dict:
    spec = _spec_path(kit_dir)
    if not spec:
        return {}
    try:
        with open(spec, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}


def bundled_kit_dir(name: str) -> Optional[Path]:
    """The directory of a bundled kit by bare name; paths are never bundled names."""
    if not name or "/" in name or os.sep in name:
        return None
    kit_dir = KITS_DIR / name
    if kit_dir.is_dir() and _spec_path(kit_dir):
        return kit_dir
    return None


def list_bundled_kits() -> List[Dict[str, str]]:
    """Name, display name, description and path of every kit shipped under kits/."""
    kits = []
    if not KITS_DIR.is_dir():
        return kits
    for kit_dir in sorted(KITS_DIR.iterdir()):
        if not kit_dir.is_dir() or not _spec_path(kit_dir):
            continue
        data = _load_spec(kit_dir)
        name = data.get("name") or kit_dir.name
        kits.append({
            "name": name,
            "display_name": data.get("displayName") or name,
            "description": data.get("description") or "",
            "path": str(kit_dir),
        })
    return kits


def kit_declares_arg(kit_dir: Path, arg: str) -> bool:
    args = _load_spec(kit_dir).get("args")
    return isinstance(args, dict) and arg in args


def resolve_kit(ref: str) -> str:
    """Resolves one kit reference to what `sbx run --kit` accepts. Raises ValueError when it cannot."""
    if not ref or ref == ".":
        raise ValueError("Empty kit reference")
    bundled = bundled_kit_dir(ref)
    if bundled:
        return str(bundled)
    if ref in AGENT_SPECS:
        raise ValueError(f"Agent '{ref}' has no kit directory under {KITS_DIR}")
    if ref.startswith(REMOTE_KIT_SCHEMES):
        return ref
    if os.path.isdir(ref):
        if not _spec_path(Path(ref)):
            raise ValueError(f"Local kit directory '{ref}' is missing spec.yaml or spec.yml")
        return str(Path(ref).resolve())
    raise ValueError(
        f"Kit '{ref}' could not be resolved as a bundled kit, agent, local directory or remote reference"
    )


def run_kits(config, extra_kits: Sequence[str] = ()) -> List[str]:
    """
    Every kit to pass to `sbx run`, in order: the primary agent's kit, the configured
    kits, their proxy overlays when proxy-managed, the generated project kit, extras.
    """
    resolved: List[str] = []

    def add(ref: str) -> None:
        if ref and ref not in resolved:
            resolved.append(ref)

    for ref in config.all_kits():
        if ref in AGENT_SPECS and not AGENT_SPECS[ref].has_kit:
            continue
        add(resolve_kit(ref))
    if config.auth_mode == AUTH_MODE_PROXY_MANAGED:
        for path in list(resolved):
            overlay = bundled_kit_dir(Path(path).name + PROXY_KIT_SUFFIX) if path.startswith(str(KITS_DIR)) else None
            if overlay:
                add(str(overlay))
    project_kit = write_project_kit(config)
    if project_kit:
        add(project_kit)
    for ref in extra_kits:
        add(resolve_kit(ref))
    return resolved


def kit_args(config, kits: Sequence[str]) -> List[str]:
    """`--kit-arg` values: auth_mode for every bundled kit that declares it, then agy.yaml's kit_args."""
    args: List[str] = []
    for path in kits:
        kit_dir = Path(path)
        if path.startswith(str(KITS_DIR)) and kit_declares_arg(kit_dir, AUTH_MODE_KIT_ARG):
            args.append(f"{kit_dir.name}.{AUTH_MODE_KIT_ARG}={config.auth_mode}")
    for key, value in config.kit_args.items():
        args.append(f"{key}={value}")
    return args


def project_kit_dir(sandbox_name: str) -> Path:
    return Path(STATE_DIR).expanduser() / "kits" / sandbox_name


def write_project_kit(config) -> Optional[str]:
    """
    Turns agy.yaml's setup_scripts into a mixin kit whose install hook runs them once,
    inside the VM, in the workspace, when the sandbox is created. Returns its path, or
    None when there is nothing to run.
    """
    if not config.setup_scripts:
        return None
    kit_dir = project_kit_dir(config.sandbox_name)
    kit_dir.mkdir(parents=True, exist_ok=True)
    script = "set -e\ncd \"${WORKSPACE_DIR:-$HOME}\"\n" + "\n".join(config.setup_scripts) + "\n"
    spec = {
        "schemaVersion": "2",
        "kind": "mixin",
        "name": PROJECT_KIT_NAME,
        "displayName": "Project setup scripts",
        "description": f"setup_scripts from agy.yaml for {config.sandbox_name}, run once when the sandbox is created",
        "setup": {
            "install": [
                {
                    "command": script,
                    "user": "agent",
                    "description": "Run agy.yaml setup_scripts in the workspace",
                }
            ]
        },
    }
    with open(kit_dir / "spec.yaml", "w", encoding="utf-8") as f:
        yaml.dump(spec, f, Dumper=_KitDumper, sort_keys=False)
    return str(kit_dir)
