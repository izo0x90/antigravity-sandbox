import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AgyConfig


def check_for_unsaved_sandbox_work(sandbox_name: str, config: "AgyConfig") -> bool:
    """
    Checks if there is ANY unsaved work or active authentication sessions in a sandbox:
    1. Uncommitted/dirty changes (`git status --porcelain`)
    2. Unpushed local commits (`git cherry -v`)
    3. Untracked local branches (`git log --branches --not --remotes --oneline`)
    4. Active TUI session auth files (.claude.json, .config/opencode/auth.json, etc.)

    Returns True if unsaved work or active session files are found, False otherwise.
    """
    # Import locally to avoid circular dependency
    from .constants import AGENT_SPECS, BANNER_WIDTH, DEFAULT_CMD_TIMEOUT
    from .runners.sbx import sandbox_exists

    if not sandbox_exists(sandbox_name):
        return False

    has_unsaved = False
    print("🔍 Code Guardian: Scanning sandbox for unsaved work and active sessions...")

    # Dynamically resolve active auth session file paths from AGENT_SPECS registry
    auth_paths = []
    for spec in AGENT_SPECS.values():
        for rel_path in spec.auth_session_files:
            clean_rel = rel_path.lstrip("/")
            for base in ["/home/agent", "/root"]:
                auth_paths.append(f"{base}/{clean_rel}")
    active_auth_files = []
    for auth_path in auth_paths:
        try:
            res = subprocess.run(
                ["sbx", "exec", sandbox_name, "test", "-e", auth_path],
                capture_output=True,
                timeout=DEFAULT_CMD_TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
            if res.returncode == 0:
                active_auth_files.append(auth_path)
        except (subprocess.SubprocessError, TimeoutError, OSError):
            pass

    if active_auth_files:
        has_unsaved = True
        print("\n" + "!" * BANNER_WIDTH)
        print("⚠️  CRITICAL: ACTIVE TUI LOGIN SESSION FILES DETECTED INSIDE SANDBOX!")
        print("Rebuilding or destroying this sandbox now will PERMANENTLY delete these sessions:")
        for auth_file in active_auth_files:
            print(f"  * {auth_file}")
        print("!" * BANNER_WIDTH)

    if not config.sbx.clone:
        return has_unsaved  # Bind-mount directly modifies host code, but auth check remains relevant

    try:
        # Check 1: Uncommitted files
        dirty_check = subprocess.run(
            ["sbx", "exec", sandbox_name, "git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=DEFAULT_CMD_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
        has_dirty_files = bool(dirty_check.stdout.strip())

        # Check 2: Unpushed commits
        commit_check = subprocess.run(
            ["sbx", "exec", sandbox_name, "git", "cherry", "-v"],
            capture_output=True,
            text=True,
            check=True,
            timeout=DEFAULT_CMD_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
        has_unpushed_commits = bool(commit_check.stdout.strip())

        # Check 3: Sandbox-only Branches
        branch_check = subprocess.run(
            [
                "sbx",
                "exec",
                sandbox_name,
                "git",
                "log",
                "--branches",
                "--not",
                "--remotes",
                "--oneline",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=DEFAULT_CMD_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
        has_sandbox_only_branches = bool(branch_check.stdout.strip())

        if has_dirty_files or has_unpushed_commits or has_sandbox_only_branches or has_unsaved:
            print("\n" + "!" * BANNER_WIDTH)
            print("⚠️  CRITICAL: UNSAVED WORK OR ACTIVE SESSIONS DETECTED INSIDE SANDBOX!")
            print("Destroying this sandbox now will PERMANENTLY delete them.")
            print("!" * BANNER_WIDTH)

            if has_dirty_files:
                print("\n📂 Uncommitted / Dirty Files inside Sandbox:")
                print(dirty_check.stdout.strip())

            if has_unpushed_commits:
                print("\n📌 Commits inside Sandbox that are NOT pushed to origin:")
                print(commit_check.stdout.strip())

            if has_sandbox_only_branches:
                print("\n🌿 Sandbox-only Branches (not synced with origin):")
                print(branch_check.stdout.strip())

            print("-" * BANNER_WIDTH)
            return True

    except Exception as e:
        print(f"\n⚠️  Note: Could not verify cloned sandbox state safely ({e}).")
        print("To be safe, we must assume there could be unsaved changes.")
        return True

    return has_unsaved
