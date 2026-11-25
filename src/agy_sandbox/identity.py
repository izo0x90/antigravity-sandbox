"""
Which sandbox a project means. agy.yaml is the only source of truth: its sandbox_id and
sandbox_version name the current sandbox outright, so nothing here parses names to find
out what a sandbox is. The one lookup is listing the versions that exist, by the prefix
every version of an identity shares.
"""
from typing import List, Optional, Tuple

from . import sandbox
from .config import AgyConfig
from .errors import AgyError


def versions(config: AgyConfig) -> List[Tuple[int, dict]]:
    """Every existing version of this project's sandbox, oldest first, with sbx's row for each."""
    if config.sandbox_id is None:
        return []
    prefix = config.with_version(1).sandbox_name[:-1]      # ...-v
    found = []
    for row in sandbox.list_sandboxes():
        name = str(row.get("name") or "")
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            found.append((int(name[len(prefix):]), row))
    return sorted(found, key=lambda item: item[0])


def legacy_sandbox(config: AgyConfig) -> Optional[str]:
    """The pre-identity sandbox of this project, if it still exists."""
    name = config.legacy_sandbox_name
    return name if sandbox.exists(name) else None


def pin(config: AgyConfig, version: int) -> AgyConfig:
    """
    The config pointed at an existing `version` instead of the one agy.yaml names. A version
    that does not exist is an error naming the ones that do: nothing creates a pinned version.
    """
    if config.sandbox_id is None:
        raise AgyError(f"'{config.project_name}' has no versioned sandboxes yet.",
                       hint="agy.yaml gets a sandbox id on the first 'agy-sandbox up' or 'recreate --next'.")
    pinned = config.with_version(version)
    if sandbox.exists(pinned.sandbox_name):
        return pinned
    have = [str(v) for v, _ in versions(config)]
    hint = f"Existing versions: {', '.join(have)}." if have else "No version of it exists yet."
    legacy = legacy_sandbox(config)
    if legacy:
        hint += f" The pre-identity sandbox '{legacy}' also still exists."
    raise AgyError(f"Version {version} of '{config.project_name}' not found.", hint=hint)
