import os
from pathlib import Path
from typing import Dict, List, Optional
import yaml

from .constants import AGENT_SPECS, REMOTE_KIT_SCHEMES

KITS_DIR = Path(__file__).resolve().parent / "kits"


def list_bundled_kits() -> List[Dict[str, str]]:
    """
    Scans the bundled kits directory and extracts metadata from each spec.yaml/spec.yml file.
    Returns a list of dictionaries with 'name', 'display_name', and 'description'.
    """
    kits = []
    if not KITS_DIR.exists() or not KITS_DIR.is_dir():
        return kits

    for kit_dir in KITS_DIR.iterdir():
        if not kit_dir.is_dir():
            continue

        spec_path = kit_dir / "spec.yaml"
        if not spec_path.exists():
            spec_path = kit_dir / "spec.yml"

        if not spec_path.exists():
            continue

        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            # Extract metadata from the standard sbx mixin fields
            name = data.get("name") or kit_dir.name
            display_name = data.get("displayName") or name
            description = data.get("description") or ""

            kits.append({
                "name": name,
                "display_name": display_name,
                "description": description,
                "path": str(kit_dir)
            })
        except Exception:
            # Gracefully ignore unparseable or corrupt spec files
            continue

    return sorted(kits, key=lambda x: x["name"])


def resolve_kit(name: str) -> Optional[str]:
    """
    Resolves a kit identifier to a canonical string (bundled directory path, agent kit_ref, or None).
    Prioritizes bundled kit directories on disk over abstract kit reference strings.
    """
    if not name:
        return None

    # 1. Check bundled kits directory first
    kit_dir = KITS_DIR / name
    if kit_dir.exists() and kit_dir.is_dir():
        if (kit_dir / "spec.yaml").exists() or (kit_dir / "spec.yml").exists():
            return str(kit_dir.resolve())

    # 2. Check AGENT_SPECS for registered agent kit references
    if name in AGENT_SPECS:
        ref = AGENT_SPECS[name].kit_ref
        spec_kit_dir = KITS_DIR / ref
        if spec_kit_dir.exists() and spec_kit_dir.is_dir():
            return str(spec_kit_dir.resolve())
        return ref

    return None


def validate_and_resolve_kits(kits: List[str]) -> List[str]:
    """
    Resolves kit entries (agent spec kit_ref, bundled name, local directory, or git/remote URL).
    Raises ValueError if a kit entry cannot be resolved.
    """
    valid_agent_refs = {spec.kit_ref for spec in AGENT_SPECS.values()}
    resolved_kits = []

    for kit in kits:
        if not kit or kit == ".":
            continue

        # Agents with no kit (e.g. `shell`) are launched via the agent arg, never as --kit.
        if kit in AGENT_SPECS and not AGENT_SPECS[kit].kit_ref:
            continue

        resolved = resolve_kit(kit)
        kit_path = resolved if resolved else kit

        if kit in AGENT_SPECS or kit_path in valid_agent_refs or kit_path.startswith(REMOTE_KIT_SCHEMES):
            resolved_kits.append(kit_path)
        elif os.path.isdir(kit_path):
            spec_yaml = os.path.join(kit_path, "spec.yaml")
            spec_yml = os.path.join(kit_path, "spec.yml")
            if not (os.path.exists(spec_yaml) or os.path.exists(spec_yml)):
                raise ValueError(
                    f"Local kit directory '{kit_path}' specified in agy.yaml is missing required 'spec.yaml' or 'spec.yml'."
                )
            resolved_kits.append(kit_path)
        else:
            raise ValueError(
                f"Kit '{kit}' specified in agy.yaml could not be resolved as a registered agent kit, bundled kit, local directory, or remote URL."
            )

    return resolved_kits
