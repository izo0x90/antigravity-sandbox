"""
Code Guardian: one look inside a sandbox before it is destroyed, reporting
login sessions and unsaved git work that would be lost. Runs a single
`sbx exec` (which starts a stopped sandbox) rather than one probe per file.
"""
from dataclasses import dataclass, field
from typing import List

from . import console, sandbox
from .agents import AGENT_SPECS
from .errors import SandboxError

AGENT_HOME = "/home/agent"


@dataclass
class ScanResult:
    session_files: List[str] = field(default_factory=list)
    dirty_files: List[str] = field(default_factory=list)
    unpushed_commits: List[str] = field(default_factory=list)
    local_branches: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def has_unsaved_work(self) -> bool:
        return bool(self.session_files or self.dirty_files or self.unpushed_commits or self.local_branches or self.error)


def session_file_paths() -> List[str]:
    paths: List[str] = []
    for spec in AGENT_SPECS.values():
        for rel in spec.session_files:
            path = f"{AGENT_HOME}/{rel.lstrip('/')}"
            if path not in paths:
                paths.append(path)
    return paths


def scan_script(check_git: bool) -> str:
    lines = ["for f in " + " ".join(f"'{p}'" for p in session_file_paths()) + "; do",
             '  [ -e "$f" ] && echo "SESSION $f"', "done"]
    if check_git:
        lines += [
            'cd "${WORKSPACE_DIR:-$PWD}" 2>/dev/null || exit 0',
            "git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0",
            "git status --porcelain | sed 's/^/DIRTY /'",
            "git cherry -v 2>/dev/null | sed 's/^/UNPUSHED /'",
            "git log --branches --not --remotes --oneline 2>/dev/null | sed 's/^/BRANCH /'",
        ]
    lines.append("exit 0")
    return "\n".join(lines)


def scan(sandbox_name: str, check_git: bool) -> ScanResult:
    """Inspects the sandbox; git checks only matter for clone-mode sandboxes (bind mounts live on the host)."""
    result = ScanResult()
    try:
        res = sandbox.exec_script(sandbox_name, scan_script(check_git))
    except SandboxError as e:
        result.error = str(e)
        return result
    if res.returncode != 0:
        result.error = (res.stderr or "").strip() or f"sbx exec exited with code {res.returncode}"
        return result
    for line in (res.stdout or "").splitlines():
        tag, _, rest = line.partition(" ")
        if tag == "SESSION":
            result.session_files.append(rest)
        elif tag == "DIRTY":
            result.dirty_files.append(rest)
        elif tag == "UNPUSHED":
            result.unpushed_commits.append(rest)
        elif tag == "BRANCH":
            result.local_branches.append(rest)
    return result


def report(result: ScanResult) -> None:
    if result.error:
        console.warn(f"Could not verify sandbox state ({result.error}). Assuming there may be unsaved work.")
        return
    if not result.has_unsaved_work:
        return
    lines = [
        "⚠️  CRITICAL: UNSAVED WORK OR ACTIVE SESSIONS DETECTED INSIDE SANDBOX!",
        "Destroying this sandbox now will PERMANENTLY delete them.",
    ]
    sections = (
        ("🔐 Active login/session files:", result.session_files),
        ("📂 Uncommitted / dirty files:", result.dirty_files),
        ("📌 Commits not pushed to origin:", result.unpushed_commits),
        ("🌿 Sandbox-only branches:", result.local_branches),
    )
    for title, items in sections:
        if items:
            lines.append("")
            lines.append(title)
            lines.extend(f"  {item}" for item in items)
    console.banner(lines)


def check_for_unsaved_sandbox_work(sandbox_name: str, clone: bool) -> bool:
    """True when destroying the sandbox would lose sessions or git work. Reports the findings."""
    if not sandbox.exists(sandbox_name):
        return False
    console.step("🔍 Code Guardian: scanning sandbox for unsaved work and active sessions...")
    result = scan(sandbox_name, check_git=clone)
    report(result)
    return result.has_unsaved_work
