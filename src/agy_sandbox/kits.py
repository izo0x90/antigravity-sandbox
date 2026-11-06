"""
Kit references and bundled kit descriptors. Pure: reads files, never runs sbx.

A kit reference in agy.yaml is one of:
  - a bundled kit name (a directory under kits/), which is also how agents are named
  - a local directory holding a v3 descriptor <dirname>.yaml
  - a remote reference (git+..., oci://..., docker.io/..., https://...)

Login mode reaches kits by attachment, not by argument: a bundled kit named
`<kit>-credentials` holds the proxy credential declarations for `<kit>` and is
attached next to it only in proxy-managed mode.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import yaml

from .agents import AGENT_SPECS
from .constants import CREDENTIALS_KIT_SUFFIX, KIT_SCHEMA_VERSION, REMOTE_KIT_SCHEMES
from .errors import KitError

KITS_DIR = Path(__file__).resolve().parent / "kits"
VOLUME_CAPABILITY = "com.docker.sandbox/volume@1"
CREDENTIAL_CAPABILITY = "com.docker.sandbox/credential@1"
LIFECYCLE_CAPABILITY = "com.docker.sandbox/lifecycle@1"
NETWORK_CAPABILITY = "com.docker.sandbox/network-policy@1"


@dataclass(frozen=True)
class KitRef:
    """One kit as sbx will receive it."""
    name: str          # basename, used for display and for finding `<name>-credentials`
    source: str        # absolute directory for local kits, the reference itself for remote ones
    bundled: bool = False


@dataclass(frozen=True)
class BundledKit:
    name: str
    display_name: str
    description: str
    path: Path
    descriptor: dict

    @property
    def internal(self) -> bool:
        """Credential kits are attached by the tool, never listed for users to add."""
        return self.name.endswith(CREDENTIALS_KIT_SUFFIX)

    def ref(self) -> KitRef:
        return KitRef(name=self.name, source=str(self.path), bundled=True)


def descriptor_path(kit_dir: Path) -> Optional[Path]:
    """A v3 kit directory carries `<dirname>.yaml` (or .yml)."""
    for suffix in (".yaml", ".yml"):
        candidate = kit_dir / f"{kit_dir.name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def load_descriptor(kit_dir: Path) -> dict:
    path = descriptor_path(kit_dir)
    if not path:
        raise KitError(f"'{kit_dir}' is not a kit directory: no {kit_dir.name}.yaml descriptor.")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        raise KitError(f"Could not read kit descriptor {path}: {e}")
    if not isinstance(data, dict):
        raise KitError(f"Kit descriptor {path} must be a mapping.")
    if str(data.get("schemaVersion")) != KIT_SCHEMA_VERSION:
        raise KitError(f"Kit descriptor {path} must be schemaVersion \"{KIT_SCHEMA_VERSION}\".")
    return data


def capabilities(descriptor: dict, type_name: str) -> List[dict]:
    """Every capability entry of one type (group blocks are flattened)."""
    found: List[dict] = []
    for entry in descriptor.get("capabilities") or []:
        if not isinstance(entry, dict):
            continue
        members = entry.get("group") if isinstance(entry.get("group"), list) else [entry]
        for member in members:
            if isinstance(member, dict) and member.get("type") == type_name:
                found.append(member)
    return found


def bundled_kits(include_internal: bool = False) -> Dict[str, BundledKit]:
    """Every kit shipped under kits/, by name, in directory order."""
    kits: Dict[str, BundledKit] = {}
    if not KITS_DIR.is_dir():
        return kits
    for kit_dir in sorted(KITS_DIR.iterdir()):
        if not kit_dir.is_dir() or not descriptor_path(kit_dir):
            continue
        descriptor = load_descriptor(kit_dir)
        kit = BundledKit(
            name=kit_dir.name,
            display_name=str(descriptor.get("displayName") or kit_dir.name),
            description=str(descriptor.get("description") or ""),
            path=kit_dir,
            descriptor=descriptor,
        )
        if kit.internal and not include_internal:
            continue
        kits[kit.name] = kit
    return kits


def bundled_kit(name: str) -> Optional[BundledKit]:
    """A bundled kit by bare name; paths are never bundled names."""
    if not name or "/" in name or os.sep in name:
        return None
    kit_dir = KITS_DIR / name
    if not kit_dir.is_dir() or not descriptor_path(kit_dir):
        return None
    return bundled_kits(include_internal=True).get(name)


def _remote_name(ref: str) -> str:
    """git+https://x/kits.git#dir=foo -> foo; docker.io/sbx/playwright:1.0 -> playwright."""
    if "#dir=" in ref:
        return ref.split("#dir=", 1)[1].split("&", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    tail = ref.rstrip("/").rsplit("/", 1)[-1]
    tail = tail.split("@", 1)[0].split(":", 1)[0]
    return tail[:-4] if tail.endswith(".git") else tail


def resolve(ref: str) -> KitRef:
    """Resolves one agy.yaml kit reference. Raises KitError when it cannot."""
    if not ref or ref == ".":
        raise KitError("Empty kit reference.")
    bundled = bundled_kit(ref)
    if bundled:
        return bundled.ref()
    if ref in AGENT_SPECS:
        raise KitError(f"Agent '{ref}' has no kit directory under {KITS_DIR}.")
    if ref.startswith(REMOTE_KIT_SCHEMES):
        return KitRef(name=_remote_name(ref), source=ref)
    if os.path.isdir(ref):
        path = Path(ref).resolve()
        load_descriptor(path)
        return KitRef(name=path.name, source=str(path))
    names = ", ".join(bundled_kits())
    raise KitError(
        f"Kit '{ref}' is not a bundled kit, a local kit directory or a remote reference.",
        hint=f"Bundled kits: {names}",
    )


def credentials_kit(kit: KitRef) -> Optional[KitRef]:
    """The bundled `<name>-credentials` kit for a bundled kit, when one exists."""
    if not kit.bundled:
        return None
    sibling = bundled_kit(kit.name + CREDENTIALS_KIT_SUFFIX)
    return sibling.ref() if sibling else None


def volume_paths(refs: Sequence[KitRef]) -> List[str]:
    """
    Mount paths of the volumes declared by local kits. sbx keeps these outside the
    container filesystem, so a recreate carries them separately. Remote kits cannot
    be read here.
    """
    paths: List[str] = []
    for ref in refs:
        if ref.source.startswith(REMOTE_KIT_SCHEMES) or not os.path.isdir(ref.source):
            continue
        for entry in capabilities(load_descriptor(Path(ref.source)), VOLUME_CAPABILITY):
            path = (entry.get("config") or {}).get("path")
            if path and path not in paths:
                paths.append(str(path))
    return paths
