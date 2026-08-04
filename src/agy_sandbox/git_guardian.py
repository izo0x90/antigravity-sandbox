import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AgyConfig


def check_for_unsaved_sandbox_work(sandbox_name: str, config: "AgyConfig") -> bool:
    """
    Checks if there is ANY unsaved work in a cloned sandbox:
    1. Uncommitted/dirty changes (`git status --porcelain`)
    2. Unpushed local commits (`git cherry -v`)
    3. Untracked local branches (`git log --branches --not --remotes --oneline`)

    Returns True if unsaved work is found, False otherwise.
    """
    if not config.sbx.clone:
        return False  # Bind-mount directly modifies host filesystem.

    # Import locally to avoid circular dependency
    from .runners.sbx import sandbox_exists

    if not sandbox_exists(sandbox_name):
        return False

    print("🔍 Code Guardian: Scanning cloned sandbox for unsaved work...")
    try:
        # Check 1: Uncommitted files
        dirty_check = subprocess.run(
            ["sbx", "exec", sandbox_name, "git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
        has_dirty_files = bool(dirty_check.stdout.strip())

        # Check 2: Unpushed commits
        commit_check = subprocess.run(
            ["sbx", "exec", sandbox_name, "git", "cherry", "-v"],
            capture_output=True,
            text=True,
            check=True,
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
        )
        has_sandbox_only_branches = bool(branch_check.stdout.strip())

        if has_dirty_files or has_unpushed_commits or has_sandbox_only_branches:
            print("\n" + "!" * 78)
            print("⚠️  CRITICAL: YOU HAVE UNSAVED OR UNPUSHED WORK DETECTED INSIDE THE CLONED SANDBOX!")
            print("Because you are in CLONE mode, these changes DO NOT exist on your host machine.")
            print("Destroying this sandbox now will PERMANENTLY delete them.")
            print("!" * 78)

            if has_dirty_files:
                print("\n📂 Uncommitted / Dirty Files inside Sandbox:")
                print(dirty_check.stdout.strip())

            if has_unpushed_commits:
                print("\n📌 Commits inside Sandbox that are NOT pushed to origin:")
                print(commit_check.stdout.strip())

            if has_sandbox_only_branches:
                print("\n🌿 Sandbox-only Branches (not synced with origin):")
                print(branch_check.stdout.strip())

            print("-" * 78)
            print("💡 To save your work, run 'git push origin' inside the sandbox container first!\n")
            return True

    except Exception as e:
        print(f"\n⚠️  Note: Could not verify cloned sandbox state safely ({e}).")
        print("To be safe, we must assume there could be unsaved changes.")
        return True

    return False
