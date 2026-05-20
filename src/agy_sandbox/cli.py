import argparse
import subprocess
import sys
from typing import List, Optional

from .config import load_config, write_default_config
from .engine import run_up, run_down, build_base_image


def init_command(args: argparse.Namespace) -> None:
    print("Running init command...")
    write_default_config()
    print("Created default agy.yaml")


def auto_init_command(args: argparse.Namespace) -> None:
    print("Running auto-init command...")
    prompt = (
        "Please analyze this project repository and automatically generate an optimal `agy.yaml` "
        "file for the agy-sandbox tool. The file should configure the appropriate python version, "
        "node version, apt_packages, and setup_scripts based on the project's codebase. "
        "Use 'default' for profile and determine the project_name from the folder name."
    )
    try:
        subprocess.run(["antigravity", prompt], check=True)
        print("Auto-init completed successfully.")
    except FileNotFoundError:
        print(
            "Error: The 'antigravity' CLI was not found. Please ensure it is installed and on your PATH."
        )
    except subprocess.CalledProcessError as e:
        print(f"Error during auto-init: {e}")


def up_command(args: argparse.Namespace) -> None:
    print("Running up command...")
    config = load_config()
    run_up(config)


def down_command(args: argparse.Namespace) -> None:
    print("Running down command...")
    config = load_config()
    run_down(config)


def update_base_command(args: argparse.Namespace) -> None:
    print("Running update-base command...")
    build_base_image()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Antigravity Sandbox CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    init_parser = subparsers.add_parser("init", help="Generate a template agy.yaml")
    init_parser.set_defaults(func=init_command)

    # auto-init
    auto_init_parser = subparsers.add_parser(
        "auto-init", help="Automatically generate agy.yaml using the Antigravity agent"
    )
    auto_init_parser.set_defaults(func=auto_init_command)

    # up
    up_parser = subparsers.add_parser("up", help="Start the sandbox container")
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

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
