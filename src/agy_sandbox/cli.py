import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from .config import AgyConfig, load_config, save_config, write_default_config
from .constants import (
    AGENT_AGY,
    AGENT_OMP,
    AGENT_PRIME_AGENT,
    AGENT_SHELL,
    AGENT_SPECS,
    DEFAULT_CONFIG_FILE,
    DEFAULT_DOCKERFILE_NAME,
    EXIT_CODE_INTERRUPTED,
    KIT_OMP,
    LOCAL_DOCKERFILE_NAME,
    discover_available_agent,
)
from .engine import (
    build_base_image,
    check_for_unsaved_sandbox_work,
    run_down,
    run_up,
)
from .prompts import build_auto_init_prompt
from .spec_inspector import (
    generate_offline_box_spec,
    run_read_only_agent_analysis,
)


def init_command(args: argparse.Namespace) -> None:
    print("Running init command...")
    omp_requested = getattr(args, "omp", False)
    prime_requested = getattr(args, "prime", False)
    
    agent = args.agent or AGENT_AGY
    if agent == AGENT_AGY:
        if prime_requested:
            agent = AGENT_PRIME_AGENT
        elif omp_requested:
            agent = AGENT_SHELL

    additional_agents = getattr(args, "with_agent", [])

    write_default_config(
        sbx_enabled=args.sbx or (agent != AGENT_AGY),
        clone_enabled=args.clone,
        kits=args.with_kit,
        agent=agent,
        additional_agents=additional_agents,
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
    prime_requested = getattr(args, "prime", False)
    omp_requested = getattr(args, "omp", False)
    sbx_enabled = args.sbx or omp_requested or prime_requested or bool(args.with_kit) or (args.agent is not None)
    clone_enabled = args.clone or sbx_enabled

    requested_agent = args.agent
    if not requested_agent:
        if prime_requested:
            requested_agent = AGENT_PRIME_AGENT
        elif omp_requested:
            requested_agent = AGENT_OMP

    discovered_spec = discover_available_agent(requested_agent)
    agent = requested_agent or (discovered_spec.identifier if discovered_spec else AGENT_AGY)

    user_kits = list(args.with_kit)
    additional_agents = getattr(args, "with_agent", [])
    for add_agent in additional_agents:
        if add_agent in AGENT_SPECS:
            kit_ref = AGENT_SPECS[add_agent].kit_ref
            if kit_ref and kit_ref not in user_kits:
                user_kits.append(kit_ref)

    if getattr(args, "omp", False) and KIT_OMP not in user_kits:
        user_kits.append(KIT_OMP)

    cwd = os.getcwd()

    prompt = build_auto_init_prompt(
        sbx_enabled=sbx_enabled,
        agent=agent,
        clone_enabled=clone_enabled,
        kits=user_kits,
    )

    spec_dict = None
    if discovered_spec:
        print(f"Analyzing project with {discovered_spec.display_name} in read-only mode...")
        spec_dict = run_read_only_agent_analysis(discovered_spec, prompt, cwd)

    if not spec_dict:
        if discovered_spec:
            print("Note: Agent analysis did not return a valid spec payload. Using local offline inspector.")
        else:
            print("Note: No AI harness CLI found on PATH. Using local offline inspector.")
        spec_dict = generate_offline_box_spec(
            cwd,
            explicit_args={
                "agent": agent,
                "sbx": sbx_enabled,
                "clone": clone_enabled,
                "kits": user_kits,
            },
        )

    # Enforce explicit flag precedence (User Flags > Inferred Spec)
    if "sbx" not in spec_dict or not isinstance(spec_dict["sbx"], dict):
        spec_dict["sbx"] = {}
    spec_dict["sbx"]["enabled"] = sbx_enabled
    spec_dict["sbx"]["agent"] = agent
    spec_dict["sbx"]["clone"] = clone_enabled
    spec_dict["agent"] = agent

    # Ensure kits has all required entries
    current_kits = list(spec_dict["sbx"].get("kits") or [])
    if agent in AGENT_SPECS:
        target_kit = AGENT_SPECS[agent].kit_ref
        if target_kit and target_kit not in current_kits:
            current_kits.append(target_kit)
    if "." not in current_kits:
        current_kits.append(".")
    for k in user_kits:
        if k not in current_kits:
            current_kits.append(k)
    spec_dict["sbx"]["kits"] = current_kits

    # Build config and save
    config = AgyConfig.from_dict(spec_dict)
    save_config(config, DEFAULT_CONFIG_FILE)

    # Check recommendations or args for Dockerfile creation
    recommendations = spec_dict.get("recommendations", {})
    dockerfile_needed = getattr(args, "dockerfile", False) or (
        isinstance(recommendations, dict) and recommendations.get("dockerfile_needed", False)
    )

    if dockerfile_needed:
        package_dir = Path(__file__).resolve().parent
        src_dockerfile = package_dir / "docker" / DEFAULT_DOCKERFILE_NAME
        dst_dockerfile = Path(LOCAL_DOCKERFILE_NAME)

        if src_dockerfile.exists():
            shutil.copy(src_dockerfile, dst_dockerfile)
            print(f"Created {DEFAULT_CONFIG_FILE} and customizable {LOCAL_DOCKERFILE_NAME}")
        else:
            print(f"Created {DEFAULT_CONFIG_FILE}")
    else:
        print(f"Created {DEFAULT_CONFIG_FILE}")

    print("Auto-init completed successfully.")


def agents_list_command(args: argparse.Namespace) -> None:
    print("Available agent harnesses:")
    for agent_id, spec in AGENT_SPECS.items():
        print(f"  * {agent_id:<12} - {spec.display_name} (kit ref: {spec.kit_ref})")


def agents_add_command(args: argparse.Namespace) -> None:
    agent_name = args.name
    if agent_name not in AGENT_SPECS:
        print(f"Error: Unknown agent '{agent_name}'.")
        print("Available agents:")
        for k, spec in AGENT_SPECS.items():
            print(f"  * {k:<12} - {spec.display_name}")
        sys.exit(1)

    try:
        config = load_config()
    except FileNotFoundError:
        print(f"Error: Configuration file {DEFAULT_CONFIG_FILE} not found. Please run 'agy-sandbox init' first.")
        sys.exit(1)

    spec = AGENT_SPECS[agent_name]
    config.sbx.enabled = True
    config.agent = agent_name
    config.sbx.agent = agent_name
    if spec.kit_ref not in config.sbx.kits:
        config.sbx.kits.append(spec.kit_ref)
        save_config(config)
        print(f"Successfully added agent kit '{agent_name}' ({spec.kit_ref}) to {DEFAULT_CONFIG_FILE}.")
    else:
        save_config(config)
        print(f"Agent kit '{agent_name}' ({spec.kit_ref}) is already configured in {DEFAULT_CONFIG_FILE}.")


def up_command(args: argparse.Namespace) -> None:
    print("Running up command...")
    config = load_config()

    if config.sbx.enabled and args.rebuild:
        if check_for_unsaved_sandbox_work(config.sandbox_name, config):
            try:
                confirm = input("Are you absolutely sure you want to destroy this sandbox and lose these changes? [y/N]: ")
            except EOFError:
                confirm = "n"
            if confirm.strip().lower() != "y":
                print("\n❌ Operation aborted. Your uncommitted/unpushed code has been protected! Stay safe, homie!")
                sys.exit(1)

    run_up(config, rebuild=args.rebuild)


def down_command(args: argparse.Namespace) -> None:
    print("Running down command...")
    config = load_config()

    if config.sbx.enabled:
        if check_for_unsaved_sandbox_work(config.sandbox_name, config):
            try:
                confirm = input("Are you absolutely sure you want to destroy this sandbox and lose these changes? [y/N]: ")
            except EOFError:
                confirm = "n"
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
    if kit_name in AGENT_SPECS:
        config.agent = kit_name
        config.sbx.agent = kit_name
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
        "--agent",
        choices=list(AGENT_SPECS.keys()),
        default=AGENT_AGY,
        help="Specify primary starting agent harness (default: agy)",
    )
    init_parser.add_argument(
        "--with-agent",
        action="append",
        default=[],
        help="Include additional agent kit in the generated config (can be repeated)",
    )
    init_parser.add_argument(
        "--sbx", action="store_true", help="Initialize the config with Docker Sandboxes (sbx) enabled"
    )
    init_parser.add_argument(
        "--omp", action="store_true", help="Initialize the config with OMP harness enabled via Docker Sandboxes (sbx)"
    )
    init_parser.add_argument(
        "--prime", action="store_true", help="Initialize the config with Prime Agent harness enabled via Docker Sandboxes (sbx)"
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
        "--agent", choices=list(AGENT_SPECS.keys()), default=None, help="Specify sandbox agent (e.g. agy, claude, opencode)"
    )
    auto_init_parser.add_argument(
        "--with-agent", action="append", default=[], help="Include additional agent kit (can be repeated)"
    )
    auto_init_parser.add_argument(
        "--sbx", action="store_true", help="Configure Docker Sandboxes (sbx) mode in auto-generated agy.yaml"
    )
    auto_init_parser.add_argument(
        "--omp", action="store_true", help="Configure OMP harness in auto-generated agy.yaml"
    )
    auto_init_parser.add_argument(
        "--prime", action="store_true", help="Configure Prime Agent harness in auto-generated agy.yaml"
    )
    auto_init_parser.add_argument(
        "--clone", action="store_true", help="Configure clone mode in auto-generated agy.yaml"
    )
    auto_init_parser.add_argument(
        "--dockerfile", action="store_true", help="Scaffold a customizable Dockerfile.agy in the project workspace"
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

    # agents
    agents_parser = subparsers.add_parser("agents", help="Manage agent harness kits")
    agents_subparsers = agents_parser.add_subparsers(dest="subcommand", required=True)

    # agents list
    agents_list_parser = agents_subparsers.add_parser("list", help="List all available agent harnesses")
    agents_list_parser.set_defaults(func=agents_list_command)

    # agents add
    agents_add_parser = agents_subparsers.add_parser("add", help="Add an agent harness kit to agy.yaml")
    agents_add_parser.add_argument("name", help="Name of the agent to add")
    agents_add_parser.set_defaults(func=agents_add_command)

    # kits
    kits_parser = subparsers.add_parser("kits", help="Manage bundled sandbox kits")
    kits_subparsers = kits_parser.add_subparsers(dest="subcommand", required=True)

    # kits list
    kits_list_parser = kits_subparsers.add_parser("list", help="List all available bundled kits")
    kits_list_parser.set_defaults(func=kits_list_command)

    # kits add
    kits_add_parser = kits_subparsers.add_parser("add", help="Add a bundled kit to agy.yaml")
    kits_add_parser.add_argument("name", help="Name of the kit to add")
    kits_add_parser.set_defaults(func=kits_add_command)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n❌ Command interrupted by user. Safe exit.")
        return EXIT_CODE_INTERRUPTED
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
