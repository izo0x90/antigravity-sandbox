import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__, engine, images
from .agents import AGENT_AGY, AGENT_SPECS, discover_available_agent
from .cli_helpers import (
    SmartArgumentParser,
    find_best_match,
    format_smart_error,
    preprocess_unquoted_comma_args,
)
from .config import AgyConfig, default_config, load_config, migration_warning, save_config
from .constants import (
    AUTH_MODE_SANDBOX_LOCAL,
    AUTH_MODES,
    DEFAULT_CONFIG_FILE,
    DEFAULT_DOCKERFILE_NAME,
    EXIT_CODE_INTERRUPTED,
    LOCAL_DOCKERFILE_NAME,
)
from .guardian import check_for_unsaved_sandbox_work
from .host_browser import HostBrowserError
from .kits import bundled_kit_dir, list_bundled_kits
from .prompts import build_auto_init_prompt
from .sandbox import SandboxError
from .spec_inspector import generate_offline_box_spec, run_read_only_agent_analysis

CONFIG_HINT = "Run 'agy-sandbox auto-init' to detect project runtimes, or 'agy-sandbox init' to write a template."


class CliError(Exception):
    """A user-facing error: printed as-is, exit code 1."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def parse_agent_list(raw_agents: Optional[List[str]]) -> List[str]:
    """['opencode,omp', 'prime-agent'] -> ['opencode', 'omp', 'prime-agent'], warning on unknown names."""
    agents: List[str] = []
    for item in raw_agents or []:
        for name in (p.strip() for p in item.split(",")):
            if not name or name in agents:
                continue
            if name not in AGENT_SPECS:
                closest = find_best_match(name, AGENT_SPECS.keys())
                hint = f" Did you mean '{closest}'?" if closest else f" Available agents: {', '.join(AGENT_SPECS)}"
                print(f"⚠️  Warning: Unknown agent harness '{name}'.{hint}", file=sys.stderr)
            agents.append(name)
    return agents


def load_or_fail() -> AgyConfig:
    try:
        config = load_config()
    except FileNotFoundError:
        raise CliError(format_smart_error(f"Configuration file '{DEFAULT_CONFIG_FILE}' not found.", CONFIG_HINT))
    warning = migration_warning(config)
    if warning:
        print(warning, file=sys.stderr)
    return config


def scaffold_dockerfile() -> None:
    src = Path(__file__).resolve().parent / "docker" / DEFAULT_DOCKERFILE_NAME
    if not src.exists():
        raise CliError(f"Default template Dockerfile not found at {src}")
    shutil.copy(src, Path(LOCAL_DOCKERFILE_NAME))
    print(f"Created customizable {LOCAL_DOCKERFILE_NAME}")


def confirm_destroy(config: AgyConfig) -> None:
    """Runs the guardian and asks before anything that destroys the sandbox."""
    if not check_for_unsaved_sandbox_work(config.sandbox_name, config):
        return
    try:
        confirm = input("Are you absolutely sure you want to destroy this sandbox and lose these changes? [y/N]: ")
    except EOFError:
        confirm = "n"
    if confirm.strip().lower() != "y":
        raise CliError("Operation aborted. Your uncommitted/unpushed code has been protected! Stay safe, homie!")


def add_kit_to_config(name: str) -> None:
    """`agents add` and `kits add` are the same operation: an agent is a kit named after it."""
    if name in AGENT_SPECS:
        spec = AGENT_SPECS[name]
        if not spec.has_kit:
            config = load_or_fail()
            config.agent = name
            save_config(config)
            print(f"Set '{name}' as the primary agent in {DEFAULT_CONFIG_FILE} (no kit needed).")
            return
    elif not bundled_kit_dir(name):
        valid = [k["name"] for k in list_bundled_kits()] + list(AGENT_SPECS)
        closest = find_best_match(name, valid)
        hint = f"Did you mean '{closest}'?" if closest else f"Available kits: {', '.join(valid)}"
        raise CliError(format_smart_error(f"Unknown kit or agent '{name}'.", hint))

    config = load_or_fail()
    if name in config.all_kits():
        print(f"'{name}' is already configured in {DEFAULT_CONFIG_FILE}.")
        return
    config.kits.append(name)
    config.kits = config.all_kits()
    save_config(config)
    print(f"Added '{name}' to kits in {DEFAULT_CONFIG_FILE}.")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def init_command(args: argparse.Namespace) -> None:
    print("Running init command...")
    agents = parse_agent_list(args.agent)
    primary = agents[0] if agents else AGENT_AGY
    config = default_config(
        agent=primary,
        kits=tuple(agents[1:]) + tuple(args.with_kit),
        clone=args.clone,
        auth_mode=args.auth,
    )
    save_config(config)
    print(f"Created default {DEFAULT_CONFIG_FILE}")
    if args.dockerfile:
        scaffold_dockerfile()


def auto_init_command(args: argparse.Namespace) -> None:
    print("Running auto-init command...")
    agents = parse_agent_list(args.agent)
    primary = agents[0] if agents else AGENT_AGY
    kits = [k for k in agents[1:] + list(args.with_kit) if k != "."]

    analyzer_target = getattr(args, "analyzer", None) or (primary if agents else None)
    analyzer = discover_available_agent(analyzer_target)
    if analyzer_target in AGENT_SPECS:
        wanted = AGENT_SPECS[analyzer_target]
        if not analyzer:
            print(f"⚠️  Requested analyzer '{wanted.display_name}' ({analyzer_target}) is not installed on PATH.")
        elif analyzer.identifier != analyzer_target:
            print(f"⚠️  Requested analyzer '{wanted.display_name}' ({analyzer_target}) is not installed on PATH.")
            print(f"-> Falling back to available host analyzer '{analyzer.display_name}' ({analyzer.identifier})...")

    cwd = os.getcwd()
    prompt = build_auto_init_prompt(agent=primary, clone_enabled=args.clone, kits=kits, auth_mode=args.auth)

    spec_dict = None
    if analyzer:
        print(f"Analyzing project with {analyzer.display_name} in read-only mode...")
        spec_dict = run_read_only_agent_analysis(analyzer, prompt, cwd)
    if not spec_dict:
        reason = (
            f"AI analyzer '{analyzer.display_name}' did not return a valid spec payload."
            if analyzer else "No AI harness CLI found on PATH."
        )
        print(f"⚠️  {reason}\n-> Falling back to local offline inspector (programmatic manifest parser)...")
        spec_dict = generate_offline_box_spec(cwd, explicit_args={"agent": primary, "clone": args.clone, "kits": kits})

    # Explicit flags win over whatever the analyzer inferred
    spec_dict["agent"] = primary
    spec_dict["clone"] = args.clone
    spec_dict["auth_mode"] = args.auth
    legacy_sbx = spec_dict.get("sbx") if isinstance(spec_dict.get("sbx"), dict) else {}
    inferred_kits = [k for k in (spec_dict.get("kits") or legacy_sbx.get("kits") or []) if k and k != "."]
    spec_dict["kits"] = inferred_kits + [k for k in kits if k not in inferred_kits]
    recommendations = spec_dict.pop("recommendations", None) or {}

    config = AgyConfig.from_dict(spec_dict)
    config.kits = config.all_kits()
    save_config(config)
    print(f"Created {DEFAULT_CONFIG_FILE}")

    if args.dockerfile or (isinstance(recommendations, dict) and recommendations.get("dockerfile_needed")):
        scaffold_dockerfile()
    print("Auto-init completed successfully.")


def up_command(args: argparse.Namespace) -> None:
    print("Running up command...")
    config = load_or_fail()
    if args.rebuild:
        confirm_destroy(config)
    if args.host_browser:
        config.host_browser.enabled = True
    for port in args.publish or []:
        if port and port not in config.ports:
            config.ports.append(port)
    code = engine.up(config, rebuild=args.rebuild)
    if code != 0:
        sys.exit(code)


def upgrade_command(args: argparse.Namespace) -> None:
    print("Running upgrade command...")
    config = load_or_fail()
    if args.host_browser:
        config.host_browser.enabled = True
    for port in args.publish or []:
        if port and port not in config.ports:
            config.ports.append(port)
    code = engine.upgrade(config)
    if code != 0:
        sys.exit(code)


def down_command(args: argparse.Namespace) -> None:
    print("Running down command...")
    config = load_or_fail()
    confirm_destroy(config)
    engine.down(config)


def update_base_command(args: argparse.Namespace) -> None:
    """Rebuilds the base image from scratch, and the agent layers of the current project when one is configured."""
    print("Running update-base command...")
    images.build_base_image(no_cache=True)
    if os.path.exists(DEFAULT_CONFIG_FILE):
        images.build_agent_layers(load_or_fail(), no_cache=True)


def agents_list_command(args: argparse.Namespace) -> None:
    print("Available agent harnesses:")
    for agent_id, spec in AGENT_SPECS.items():
        kit = f"kit: {agent_id}" if spec.has_kit else "no kit"
        print(f"  * {agent_id:<12} - {spec.display_name} ({kit})")


def kits_list_command(args: argparse.Namespace) -> None:
    kits = list_bundled_kits()
    if not kits:
        print("No bundled kits found.")
        return
    print("Available bundled kits:")
    for kit in kits:
        print(f"  * {kit['name']:<18} - {kit['display_name']} ({kit['description']})")


def add_command(args: argparse.Namespace) -> None:
    add_kit_to_config(args.name)


def config_migrate_command(args: argparse.Namespace) -> None:
    try:
        config = load_config()
    except FileNotFoundError:
        raise CliError(format_smart_error(f"Configuration file '{DEFAULT_CONFIG_FILE}' not found.", CONFIG_HINT))
    if not config.migration_notes:
        print(f"{DEFAULT_CONFIG_FILE} is already in the current layout.")
        return
    for note in config.migration_notes:
        print(f"   - {note}")
    save_config(config)
    print(f"Rewrote {DEFAULT_CONFIG_FILE} in the current layout.")


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def _add_init_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent", action="append",
        help="Agent harness(es) to install (e.g. opencode,omp,prime-agent). The first one is primary.",
    )
    parser.add_argument("--clone", action="store_true", help="Work on a private git clone inside the sandbox")
    parser.add_argument(
        "--auth", choices=AUTH_MODES, default=AUTH_MODE_SANDBOX_LOCAL,
        help="Login mode: sandbox-local (log in inside the sandbox) or proxy-managed (host-held sbx secrets)",
    )
    parser.add_argument("--dockerfile", action="store_true", help=f"Scaffold a customizable {LOCAL_DOCKERFILE_NAME}")
    parser.add_argument("--with-kit", action="append", default=[], help="Add a bundled or local mixin kit (repeatable)")


def build_parser() -> argparse.ArgumentParser:
    parser = SmartArgumentParser(description="Antigravity Sandbox CLI")
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=SmartArgumentParser)

    p = sub.add_parser("init", help=f"Write a template {DEFAULT_CONFIG_FILE}")
    _add_init_options(p)
    p.set_defaults(func=init_command)

    p = sub.add_parser("auto-init", help=f"Generate {DEFAULT_CONFIG_FILE} by inspecting the project read-only")
    p.add_argument("--analyzer", help="Host agent used for the read-only analysis (e.g. opencode, claude, agy)")
    _add_init_options(p)
    p.set_defaults(func=auto_init_command)

    p = sub.add_parser("up", help="Create or resume the sandbox and attach to it")
    p.add_argument("--rebuild", action="store_true", help="Rebuild the image and recreate the sandbox from scratch (see 'upgrade' to keep its contents)")
    p.add_argument("--host-browser", action="store_true", help="Drive a Chrome on the host from the sandbox")
    p.add_argument("-p", "--publish", action="append", default=[], help="Publish a sandbox port to the host (e.g. 3000:3000)")
    p.set_defaults(func=up_command)

    p = sub.add_parser(
        "upgrade",
        help="Apply agy.yaml changes (kits, auth_mode, env, ports, host browser) to the existing sandbox, keeping everything inside it",
    )
    p.add_argument("--host-browser", action="store_true", help="Drive a Chrome on the host from the sandbox")
    p.add_argument("-p", "--publish", action="append", default=[], help="Publish a sandbox port to the host (e.g. 3000:3000)")
    p.set_defaults(func=upgrade_command)

    p = sub.add_parser("down", help="Stop and remove the sandbox")
    p.set_defaults(func=down_command)

    p = sub.add_parser("update-base", help="Rebuild the agy-base image (and the project's agent layers) from scratch")
    p.set_defaults(func=update_base_command)

    agents = sub.add_parser("agents", help="Agent harnesses").add_subparsers(dest="subcommand", required=True)
    agents.add_parser("list", help="List agent harnesses").set_defaults(func=agents_list_command)
    p = agents.add_parser("add", help=f"Add an agent harness to {DEFAULT_CONFIG_FILE}")
    p.add_argument("name")
    p.set_defaults(func=add_command)

    kits = sub.add_parser("kits", help="Bundled mixin kits").add_subparsers(dest="subcommand", required=True)
    kits.add_parser("list", help="List bundled kits").set_defaults(func=kits_list_command)
    p = kits.add_parser("add", help=f"Add a kit to {DEFAULT_CONFIG_FILE}")
    p.add_argument("name")
    p.set_defaults(func=add_command)

    cfg = sub.add_parser("config", help=f"Manage {DEFAULT_CONFIG_FILE}").add_subparsers(dest="subcommand", required=True)
    cfg.add_parser("migrate", help=f"Rewrite a pre-0.5 {DEFAULT_CONFIG_FILE} in the current layout").set_defaults(
        func=config_migrate_command
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # Progress lines are interleaved with sbx's own output; keep them in order even when piped.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    raw_argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(preprocess_unquoted_comma_args(raw_argv))
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n❌ Command interrupted by user. Safe exit.")
        return EXIT_CODE_INTERRUPTED
    except CliError as e:
        print(str(e) if str(e).startswith("❌") else f"❌ {e}", file=sys.stderr)
        return 1
    except (SandboxError, HostBrowserError, ValueError, OSError) as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
