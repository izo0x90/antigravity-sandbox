import argparse
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__, console, engine, identity, sandbox, upgrade
from .agents import AGENT_AGY, AGENT_SPECS, discover_available_agent
from .cli_helpers import (
    SmartArgumentParser,
    find_best_match,
    preprocess_unquoted_comma_args,
)
from .config import AgyConfig, default_config, load_config, migration_warning, save_config
from .constants import (
    AUTH_MODE_SANDBOX_LOCAL,
    AUTH_MODES,
    DEFAULT_CONFIG_FILE,
    EXIT_CODE_INTERRUPTED,
    LOCAL_DOCKERFILE_NAME,
)
from .errors import AgyError, ConfigError
from .guardian import check_for_unsaved_sandbox_work
from .kits import bundled_kit, bundled_kits
from .prompts import build_auto_init_prompt
from .spec_inspector import generate_offline_box_spec, run_read_only_agent_analysis
from .workload import PACKAGED_DOCKERFILE


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
                console.warn(f"Unknown agent harness '{name}'.{hint}")
            agents.append(name)
    return agents


def load_or_fail() -> AgyConfig:
    config = load_config()
    warning = migration_warning(config)
    if warning:
        console.warn(warning)
    return config


def scaffold_dockerfile() -> None:
    if not PACKAGED_DOCKERFILE.exists():
        raise ConfigError(f"Default template Dockerfile not found at {PACKAGED_DOCKERFILE}")
    shutil.copy(PACKAGED_DOCKERFILE, Path(LOCAL_DOCKERFILE_NAME))
    console.step(f"Created customizable {LOCAL_DOCKERFILE_NAME}")


def confirm_destroy(config: AgyConfig, assume_yes: bool = False) -> None:
    """Runs the guardian and asks before anything that destroys the sandbox (`--yes` answers for a script)."""
    if not check_for_unsaved_sandbox_work(config.sandbox_name, clone=config.clone):
        return
    if assume_yes:
        console.warn("Proceeding without confirmation (--yes).")
        return
    try:
        confirm = input("Are you absolutely sure you want to destroy this sandbox and lose these changes? [y/N]: ")
    except EOFError:
        confirm = "n"
    if confirm.strip().lower() != "y":
        raise AgyError("Operation aborted. Your uncommitted/unpushed code has been protected! Stay safe, homie!")


def pin_version(config: AgyConfig, args: argparse.Namespace) -> AgyConfig:
    """`--version N`: act on that existing version instead of the one agy.yaml points at."""
    version = getattr(args, "sandbox_version", None)
    return config if version is None else identity.pin(config, version)


def apply_session_flags(config: AgyConfig, args: argparse.Namespace) -> None:
    if getattr(args, "host_browser", False):
        config.host_browser.enabled = True
    for port in getattr(args, "publish", None) or []:
        if port and port not in config.ports:
            config.ports.append(port)


def add_kit_to_config(name: str) -> None:
    """`agents add` and `kits add` are the same operation: an agent is a kit named after it."""
    if name in AGENT_SPECS:
        spec = AGENT_SPECS[name]
        if not spec.has_kit:
            config = load_or_fail()
            config.agent = name
            save_config(config)
            console.step(f"Set '{name}' as the primary agent in {DEFAULT_CONFIG_FILE} (no kit needed).")
            return
    else:
        kit = bundled_kit(name)
        if not kit or kit.internal:
            valid = list(bundled_kits()) + list(AGENT_SPECS)
            closest = find_best_match(name, valid)
            hint = f"Did you mean '{closest}'?" if closest else f"Available kits: {', '.join(valid)}"
            raise ConfigError(f"Unknown kit or agent '{name}'.", hint=hint)

    config = load_or_fail()
    if name in config.all_kits():
        console.info(f"'{name}' is already configured in {DEFAULT_CONFIG_FILE}.")
        return
    config.kits.append(name)
    config.kits = config.all_kits()
    save_config(config)
    console.step(f"Added '{name}' to kits in {DEFAULT_CONFIG_FILE}.")


def exit_with(code: int) -> None:
    if code != 0:
        sys.exit(code)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def init_command(args: argparse.Namespace) -> None:
    agents = parse_agent_list(args.agent)
    primary = agents[0] if agents else AGENT_AGY
    config = default_config(
        agent=primary,
        kits=tuple(agents[1:]) + tuple(args.with_kit),
        clone=args.clone,
        auth_mode=args.auth,
    )
    save_config(config)
    console.step(f"Created default {DEFAULT_CONFIG_FILE}")
    if args.dockerfile:
        scaffold_dockerfile()


def auto_init_command(args: argparse.Namespace) -> None:
    agents = parse_agent_list(args.agent)
    primary = agents[0] if agents else AGENT_AGY
    kits = [k for k in agents[1:] + list(args.with_kit) if k != "."]

    analyzer_target = getattr(args, "analyzer", None) or (primary if agents else None)
    analyzer = discover_available_agent(analyzer_target)
    if analyzer_target in AGENT_SPECS:
        wanted = AGENT_SPECS[analyzer_target]
        if not analyzer:
            console.warn(f"Requested analyzer '{wanted.display_name}' ({analyzer_target}) is not installed on PATH.")
        elif analyzer.identifier != analyzer_target:
            console.warn(f"Requested analyzer '{wanted.display_name}' ({analyzer_target}) is not installed on PATH.")
            console.info(f"-> Falling back to available host analyzer '{analyzer.display_name}' ({analyzer.identifier})...")

    cwd = str(Path.cwd())
    prompt = build_auto_init_prompt(agent=primary, clone_enabled=args.clone, kits=kits, auth_mode=args.auth)

    spec_dict = None
    if analyzer:
        console.step(f"Analyzing project with {analyzer.display_name} in read-only mode...")
        spec_dict = run_read_only_agent_analysis(analyzer, prompt, cwd)
    if not spec_dict:
        reason = (
            f"AI analyzer '{analyzer.display_name}' did not return a valid spec payload."
            if analyzer else "No AI harness CLI found on PATH."
        )
        console.warn(f"{reason}\n-> Falling back to local offline inspector (programmatic manifest parser)...")
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
    console.step(f"Created {DEFAULT_CONFIG_FILE}")

    if args.dockerfile or (isinstance(recommendations, dict) and recommendations.get("dockerfile_needed")):
        scaffold_dockerfile()
    console.step("Auto-init completed successfully.")


def up_command(args: argparse.Namespace) -> None:
    config = pin_version(load_or_fail(), args)
    if args.rebuild:
        confirm_destroy(config, args.yes)
    apply_session_flags(config, args)
    exit_with(engine.up(config, rebuild=args.rebuild, cwd=Path.cwd(), attach=not args.no_attach,
                        config_path=DEFAULT_CONFIG_FILE))


def upgrade_command(args: argparse.Namespace) -> None:
    config = pin_version(load_or_fail(), args)
    apply_session_flags(config, args)
    exit_with(upgrade.upgrade(config, cwd=Path.cwd(), attach=not args.no_attach))


def recreate_command(args: argparse.Namespace) -> None:
    config = pin_version(load_or_fail(), args)
    apply_session_flags(config, args)
    if args.next:
        # Side by side: the current sandbox is read, never removed, so there is nothing to confirm.
        exit_with(upgrade.recreate_next(config, cwd=Path.cwd(), attach=not args.no_attach, config_path=DEFAULT_CONFIG_FILE))
        return
    confirm_destroy(config, args.yes)   # in-place recreate still stops and removes the sandbox
    exit_with(upgrade.recreate(config, cwd=Path.cwd(), attach=not args.no_attach))


def down_command(args: argparse.Namespace) -> None:
    config = pin_version(load_or_fail(), args)
    confirm_destroy(config, args.yes)
    engine.down(config)


def versions_command(args: argparse.Namespace) -> None:
    """Every sandbox this project has: the versions under its id, and a pre-identity one if it is still around."""
    config = load_or_fail()
    sandbox.require()
    found = identity.versions(config)
    legacy = identity.legacy_sandbox(config)
    if config.sandbox_id is None:
        console.info(f"'{config.project_name}' has no sandbox id yet; 'agy-sandbox up' or 'recreate --next' gives it one.")
    else:
        console.info(f"Sandbox versions of '{config.project_name}' (id {config.sandbox_id}):")
    if not found and not legacy:
        console.info("   no sandbox exists.")
    for version, row in found:
        current = "  <- agy.yaml" if version == config.sandbox_version else ""
        console.info(f"   v{version:<4} {row.get('name', ''):<60} {str(row.get('state') or row.get('status') or ''):<8}{current}")
    if legacy:
        current = "  <- agy.yaml" if config.sandbox_id is None else f"  pre-identity; remove with 'sbx rm {legacy}' when done"
        console.info(f"   {'pre':<5} {legacy:<60} {'':<8}{current}")


def agents_list_command(args: argparse.Namespace) -> None:
    console.info("Available agent harnesses:")
    for agent_id, spec in AGENT_SPECS.items():
        kit = f"kit: {agent_id}" if spec.has_kit else "no kit"
        console.info(f"  * {agent_id:<12} - {spec.display_name} ({kit})")


def kits_list_command(args: argparse.Namespace) -> None:
    kits = bundled_kits()
    if not kits:
        console.info("No bundled kits found.")
        return
    console.info("Available bundled kits:")
    for kit in kits.values():
        console.info(f"  * {kit.name:<18} - {kit.display_name} ({kit.description})")


def add_command(args: argparse.Namespace) -> None:
    add_kit_to_config(args.name)


def config_migrate_command(args: argparse.Namespace) -> None:
    config = load_config()
    if not config.migration_notes:
        console.info(f"{DEFAULT_CONFIG_FILE} is already in the current layout.")
        return
    for note in config.migration_notes:
        console.info(f"   - {note}")
    save_config(config)
    console.step(f"Rewrote {DEFAULT_CONFIG_FILE} in the current layout.")


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


def _add_session_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host-browser", action="store_true", help="Drive a Chrome on the host from the sandbox")
    parser.add_argument("-p", "--publish", action="append", default=[], help="Publish a sandbox port to the host (e.g. 3000:3000)")
    parser.add_argument("--no-attach", action="store_true", help="Do the work and return instead of attaching to the sandbox (for scripts)")


def _add_version_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version", dest="sandbox_version", type=int, metavar="N",
                        help="Act on version N of this project's sandbox instead of the one agy.yaml points at (see 'versions')")


def _add_yes_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation when the guardian finds unsaved work (for scripts)")


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
    p.add_argument("--rebuild", action="store_true", help="Recreate the sandbox from scratch (see 'recreate' to keep its contents)")
    _add_session_options(p)
    _add_version_option(p)
    _add_yes_option(p)
    p.set_defaults(func=up_command)

    p = sub.add_parser("upgrade", help="Apply agy.yaml additions (kits, env, ports, host browser) to the existing sandbox in place")
    _add_session_options(p)
    _add_version_option(p)
    p.set_defaults(func=upgrade_command)

    p = sub.add_parser("recreate", help="Rebuild the sandbox from a snapshot of itself so removed kits, changed kit arguments or a changed login mode apply without losing its contents")
    p.add_argument("--next", action="store_true",
                   help="Build the next version of the sandbox beside this one instead of replacing it, and point agy.yaml at it; "
                        "the current sandbox is left untouched. A sandbox built by agy-sandbox 0.5 (root sessions) is converted "
                        "to the agent user in the copy.")
    _add_session_options(p)
    _add_version_option(p)
    _add_yes_option(p)
    p.set_defaults(func=recreate_command)

    p = sub.add_parser("down", help="Stop and remove the sandbox")
    _add_version_option(p)
    _add_yes_option(p)
    p.set_defaults(func=down_command)

    sub.add_parser("versions", help="List this project's sandbox versions and which one agy.yaml points at").set_defaults(func=versions_command)

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
        print("\n❌ Command interrupted by user. Safe exit.", file=sys.stderr)
        return EXIT_CODE_INTERRUPTED
    except AgyError as e:
        print(f"❌ {e.message}", file=sys.stderr)
        if e.hint:
            print(f"💡 {e.hint}", file=sys.stderr)
        return 1
    except OSError as e:
        # Filesystem failures outside the error model (unreadable agy.yaml, read-only state dir)
        print(f"❌ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
