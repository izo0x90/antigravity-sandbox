import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .config import load_config, write_default_config
from .constants import (
    AGENT_AGY,
    AGENT_SHELL,
    DEFAULT_CONFIG_FILE,
    DEFAULT_DOCKERFILE_NAME,
    KIT_OMP,
    LOCAL_DOCKERFILE_NAME,
)
from .engine import (
    build_base_image,
    check_for_unsaved_sandbox_work,
    run_down,
    run_up,
)
from .prompts import build_auto_init_prompt


def init_command(args: argparse.Namespace) -> None:
    print("Running init command...")
    omp_requested = getattr(args, "omp", False)
    agent = AGENT_SHELL if omp_requested else AGENT_AGY

    write_default_config(
        sbx_enabled=args.sbx,
        clone_enabled=args.clone,
        kits=args.with_kit,
        agent=agent,
        omp=omp_requested,
    )

    if args.dockerfile:
        package_dir = Path(__file__).resolve().parent
        src_dockerfile = package_dir / "docker" / DEFAULT_DOCKERFILE_NAME
        dst_dockerfile = Path(LOCAL_DOCKERFILE_NAME)

        if src_dockerfile.exists():
            shutil.copy(src_dockerfile, dst_dockerfile)
            print(f"Created default {DEFAULT_CONFIG_FILE} and customizable {LOCAL_DOCKERFILE_NAME}")
        else:
            print(f"Error: Default template Dockerfile not found at {src_dockerfile}")
            sys.exit(1)
    else:
        print(f"Created default {DEFAULT_CONFIG_FILE}")


def auto_init_command(args: argparse.Namespace) -> None:
    print("Running auto-init command...")
    sbx_enabled = args.sbx or getattr(args, "omp", False) or bool(args.with_kit) or (args.agent is not None)
    clone_enabled = args.clone or sbx_enabled

    agent = args.agent
    if not agent:
        agent = AGENT_SHELL if getattr(args, "omp", False) else AGENT_AGY

    kits = list(args.with_kit)
    if getattr(args, "omp", False) and KIT_OMP not in kits:
        kits.append(KIT_OMP)

    prompt = build_auto_init_prompt(
        sbx_enabled=sbx_enabled,
        agent=agent,
        clone_enabled=clone_enabled,
        kits=kits,
    )

    try:
        current_dir = os.getcwd()
        subprocess.run(["agy", "--add-dir", current_dir, "--print", prompt], check=True)
        print("Auto-init completed successfully.")
    except FileNotFoundError:
        print("Error: The 'agy' CLI was not found. Please ensure it is installed and on your PATH.")
    except subprocess.CalledProcessError as e:
        print(f"Error during auto-init: {e}")


def up_command(args: argparse.Namespace) -> None:
    print("Running up command...")
    config = load_config()

    if config.sbx.enabled and args.rebuild:
        if check_for_unsaved_sandbox_work(config.sandbox_name, config):
            confirm = input("Are you absolutely sure you want to destroy this sandbox and lose these changes? [y/N]: ")
            if confirm.strip().lower() != "y":
                print("\n❌ Operation aborted. Your uncommitted/unpushed code has been protected! Stay safe, homie!")
                sys.exit(1)

    run_up(config, rebuild=args.rebuild)


def down_command(args: argparse.Namespace) -> None:
    print("Running down command...")
    config = load_config()

    if config.sbx.enabled:
        if check_for_unsaved_sandbox_work(config.sandbox_name, config):
            confirm = input("Are you absolutely sure you want to destroy this sandbox and lose these changes? [y/N]: ")
            if confirm.strip().lower() != "y":
                print("\n❌ Operation aborted. Your uncommitted/unpushed code has been protected! Stay safe, homie!")
                sys.exit(1)

    run_down(config)


def update_base_command(args: argparse.Namespace) -> None:
    print("Running update-base command...")
    build_base_image()


def kits_list_command(args: argparse.Namespace) -> None:
    from .kits import list_bundled_kits

    kits = list_bundled_kits()
    if not kits:
        print("No bundled kits found.")
        return

    print("Available bundled kits:")
    for kit in kits:
        print(f"  * {kit['name']:<18} - {kit['display_name']} ({kit['description']})")


def kits_add_command(args: argparse.Namespace) -> None:
    from .config import save_config
    from .kits import list_bundled_kits, resolve_kit

    kit_name = args.name
    resolved = resolve_kit(kit_name)
    if not resolved:
        print(f"Error: Unknown kit '{kit_name}'.")
        kits = list_bundled_kits()
        if kits:
            print("Available kits are:")
            for k in kits:
                print(f"  * {k['name']}")
        sys.exit(1)

    try:
        config = load_config()
    except FileNotFoundError:
        print(f"Error: Configuration file {DEFAULT_CONFIG_FILE} not found. Please run 'agy-sandbox init' first.")
        sys.exit(1)

    config.sbx.enabled = True
    if kit_name == KIT_OMP:
        config.sbx.agent = AGENT_SHELL
    if kit_name not in config.sbx.kits:
        config.sbx.kits.append(kit_name)
        save_config(config)
        print(f"Successfully added kit '{kit_name}' to {DEFAULT_CONFIG_FILE} and enabled sbx mode.")
    else:
        save_config(config)
        print(f"Kit '{kit_name}' is already configured in {DEFAULT_CONFIG_FILE}.")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Antigravity Sandbox CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    init_parser = subparsers.add_parser("init", help="Generate a template agy.yaml")
    init_parser.add_argument(
        "--sbx", action="store_true", help="Initialize the config with Docker Sandboxes (sbx) enabled"
    )
    init_parser.add_argument(
        "--omp", action="store_true", help="Initialize the config with OMP harness enabled via Docker Sandboxes (sbx)"
    )
    init_parser.add_argument(
        "--clone", action="store_true", help="Initialize the config with clone mode enabled inside the sandbox"
    )
    init_parser.add_argument(
        "--dockerfile", action="store_true", help="Scaffold a customizable Dockerfile.agy in the project workspace"
    )
    init_parser.add_argument(
        "--with-kit", action="append", default=[], help="Seed a bundled kit in the generated config (can be repeated)"
    )
    init_parser.set_defaults(func=init_command)

    # auto-init
    auto_init_parser = subparsers.add_parser(
        "auto-init", help="Automatically generate agy.yaml using the Antigravity agent"
    )
    auto_init_parser.add_argument(
        "--sbx", action="store_true", help="Configure Docker Sandboxes (sbx) mode in auto-generated agy.yaml"
    )
    auto_init_parser.add_argument(
        "--omp", action="store_true", help="Configure OMP harness in auto-generated agy.yaml"
    )
    auto_init_parser.add_argument(
        "--clone", action="store_true", help="Configure clone mode in auto-generated agy.yaml"
    )
    auto_init_parser.add_argument(
        "--agent", type=str, default=None, help="Specify sandbox agent (e.g. agy, shell)"
    )
    auto_init_parser.add_argument(
        "--with-kit", action="append", default=[], help="Add bundled or local mixin kit (can be repeated)"
    )
    auto_init_parser.set_defaults(func=auto_init_command)

    # up
    up_parser = subparsers.add_parser("up", help="Start the sandbox container")
    up_parser.add_argument(
        "--rebuild", action="store_true", help="Force rebuild of custom template/image and sandbox"
    )
    up_parser.set_defaults(func=up_command)

    # down
    down_parser = subparsers.add_parser(
        "down", help="Stop and remove the sandbox container"
    )
    down_parser.set_defaults(func=down_command)

    # update-base
    update_base_parser = subparsers.add_parser(
        "update-base", help="Rebuild the agy-base Docker image"
    )
    update_base_parser.set_defaults(func=update_base_command)

    # kits
    kits_parser = subparsers.add_parser("kits", help="Manage bundled sandbox kits")
    kits_subparsers = kits_parser.add_subparsers(dest="subcommand", required=True)

    # kits list
    kits_list_parser = kits_subparsers.add_parser("list", help="List all available bundled kits")
    kits_list_parser.set_defaults(func=kits_list_command)

    # kits add
    kits_add_parser = subparsers.add_parser("add", help="Add a bundled kit to agy.yaml")
    kits_add_parser.add_argument("name", help="Name of the kit to add")
    kits_add_parser.set_defaults(func=kits_add_command)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n❌ Command interrupted by user. Safe exit.")
        return 130
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
