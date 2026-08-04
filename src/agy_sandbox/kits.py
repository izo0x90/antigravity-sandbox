import os
import yaml
from pathlib import Path
from typing import List, Dict, Optional


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


def resolve_kit(name: str) -> Optional[Path]:
    """
    Resolves a short kit name to its bundled directory if it exists as a valid kit.
    Returns the absolute Path of the kit directory, or None.
    """
    if not name:
        return None

    kit_dir = KITS_DIR / name
    if kit_dir.exists() and kit_dir.is_dir():
        if (kit_dir / "spec.yaml").exists() or (kit_dir / "spec.yml").exists():
            return kit_dir.resolve()

    return None


def validate_and_resolve_kits(kits: List[str]) -> List[str]:
    """
    Resolves kit entries (bundled name, local directory, or git URL) and validates local specs.
    Returns a list of valid kit paths for sbx execution.
    """
    resolved_kits = []
    for kit in kits:
        resolved = resolve_kit(kit)
        kit_path = str(resolved) if resolved else kit

        if os.path.isdir(kit_path):
            spec_yaml = os.path.join(kit_path, "spec.yaml")
            spec_yml = os.path.join(kit_path, "spec.yml")
            if not (os.path.exists(spec_yaml) or os.path.exists(spec_yml)):
                print(f"-> Note: Local kit '{kit_path}' specified in agy.yaml but no 'spec.yaml' or 'spec.yml' found. Skipping local kit.")
                continue

        resolved_kits.append(kit_path)
    return resolved_kits
