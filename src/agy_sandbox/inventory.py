"""
File inventories of a sandbox, and the comparison that shows what a recreate lost.

An inventory is the output of one `find` inside the sandbox: `<type> <size> <path>`
per entry. Two inventories are compared path by path; a home directory that moved
(root's home becoming the agent's, see legacy.py) is mapped before comparing, and
paths the kits rewrite on every create are left out. The result is reported, never
acted on: the person decides whether a difference matters.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# Not part of a sandbox's state: kernel/runtime views, scratch space, sbx's own logs
# and the kit manifests and hook scripts sbx lays down on every create.
PRUNED_DIRS = ("/proc", "/sys", "/dev", "/run", "/tmp", "/var/tmp", "/var/log", "/var/lib/sbx", "/var/cache")
KIT_MANAGED_PREFIXES = ("/usr/share/sandbox/kit/", "/etc/durable-startup.d/", "/etc/sandbox-persistent.sh", "/var/log/sbx")
MAX_LISTED = 40


@dataclass(frozen=True)
class Entry:
    kind: str   # find's %y: f file, d dir, l symlink, ...
    size: int


@dataclass
class InventoryDiff:
    compared: int = 0
    added: int = 0
    missing: List[str] = field(default_factory=list)   # in the old inventory, absent from the new one
    changed: List[str] = field(default_factory=list)   # regular files present in both with a different size

    @property
    def clean(self) -> bool:
        return not self.missing


def script(extra_paths: Sequence[str] = ()) -> str:
    """
    The inventory command. `-xdev` keeps `find` on the root filesystem, so kit volumes
    and a clone workspace, which sbx mounts separately, are listed by explicit path.
    """
    prune = " -o ".join(f"-path '{d}'" for d in PRUNED_DIRS)
    lines = [f"find / -xdev \\( {prune} \\) -prune -o -printf '%y %s %p\\n' 2>/dev/null"]
    for path in extra_paths:
        lines.append(f"[ -e '{path}' ] && find '{path}' -printf '%y %s %p\\n' 2>/dev/null")
    lines.append("exit 0")
    return "\n".join(lines)


def parse(text: str) -> Dict[str, Entry]:
    entries: Dict[str, Entry] = {}
    for line in text.splitlines():
        parts = line.split(" ", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        entries[parts[2]] = Entry(kind=parts[0], size=int(parts[1]))
    return entries


def _mapped(path: str, moved_home: Optional[Tuple[str, str]]) -> str:
    if moved_home:
        src, dst = moved_home
        if path == src or path.startswith(src + "/"):
            return dst + path[len(src):]
    return path


def compare(old_text: str, new_text: str, *, moved_home: Optional[Tuple[str, str]] = None,
            ignore_prefixes: Sequence[str] = KIT_MANAGED_PREFIXES) -> InventoryDiff:
    old, new = parse(old_text), parse(new_text)
    diff = InventoryDiff()
    seen_new = set()
    for path, entry in sorted(old.items()):
        if path.startswith(tuple(ignore_prefixes)):
            continue
        target = _mapped(path, moved_home)
        if moved_home and path == moved_home[0]:
            continue   # the old home directory itself is expected to be gone
        diff.compared += 1
        counterpart = new.get(target)
        if counterpart is None:
            diff.missing.append(path)
            continue
        seen_new.add(target)
        if entry.kind == "f" and counterpart.kind == "f" and entry.size != counterpart.size:
            diff.changed.append(target)
    diff.added = sum(1 for p in new if p not in seen_new and not p.startswith(tuple(ignore_prefixes)))
    return diff


def render(diff: InventoryDiff, *, full: bool = False) -> List[str]:
    """Lines for the console (first MAX_LISTED of each list) or for the report file (everything)."""
    limit = None if full else MAX_LISTED
    lines = [f"Compared {diff.compared} entries from the old sandbox: {len(diff.missing)} missing, "
             f"{len(diff.changed)} changed in size, {diff.added} new in the new sandbox."]
    for title, items in (("Missing in the new sandbox:", diff.missing), ("Changed in size (merged or kit-rewritten files):", diff.changed)):
        if not items:
            continue
        lines.append(title)
        lines += [f"   {p}" for p in items[:limit]]
        if limit is not None and len(items) > limit:
            lines.append(f"   ... {len(items) - limit} more in the report file")
    return lines
