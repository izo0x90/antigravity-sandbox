"""
Sandbox lifecycle.

  up       builds the project image, loads it into sbx, creates the sandbox or resumes it
  upgrade  applies agy.yaml changes (kits, login mode, env, ports, host browser) to an
           existing sandbox without losing anything inside it
  down     removes the sandbox

Everything agent-specific comes from the kits; everything login-specific from `auth_mode`.
"""
import os
import shutil
import time
from pathlib import Path
from typing import List, Set, Tuple

from . import images, sandbox
from .config import AgyConfig
from .constants import (
    AUTH_MODE_PROXY_MANAGED,
    AUTH_MODE_SANDBOX_LOCAL,
    KIT_CHROME_DEVTOOLS,
    KIT_CHROME_HOST,
    PROXY_PLACEHOLDER_ENV,
    UPGRADE_STAGING_DIR,
    UPGRADE_TAG_PREFIX,
)
from .host_browser import HostBrowser, HostBrowserError
from .kits import kit_args, kit_volume_paths, run_kits


# ---------------------------------------------------------------------------
# pieces
# ---------------------------------------------------------------------------
def session_env(config: AgyConfig) -> List[str]:
    """
    Environment for every session: agy.yaml's `env`, and in sandbox-local mode blank
    overrides for the proxy placeholders the built-in shell injects, so no agent picks
    up a host credential the user did not ask for.
    """
    env = list(config.env)
    if config.auth_mode == AUTH_MODE_SANDBOX_LOCAL:
        env += [f"{var}=" for var in PROXY_PLACEHOLDER_ENV]
    return env


def host_browser_for(config: AgyConfig) -> HostBrowser:
    settings = config.host_browser
    return HostBrowser.for_sandbox(config.sandbox_name, settings.port, settings.headless)


def validate_host_browser(config: AgyConfig) -> None:
    if KIT_CHROME_DEVTOOLS not in config.all_kits():
        raise HostBrowserError(
            f"--host-browser needs the '{KIT_CHROME_DEVTOOLS}' kit, which registers the browser MCP server. "
            f"Run: agy-sandbox kits add {KIT_CHROME_DEVTOOLS}"
        )


def host_browser_kit_args(config: AgyConfig) -> List[str]:
    return [f"{KIT_CHROME_HOST}.host_port={config.host_browser.port}"]


def desired_kits(config: AgyConfig) -> Tuple[List[str], List[str]]:
    """The kits and kit arguments a sandbox for this config should have."""
    extra = [KIT_CHROME_HOST] if config.host_browser.enabled else []
    kits = run_kits(config, extra_kits=extra)
    args = kit_args(config, kits)
    if config.host_browser.enabled:
        args += host_browser_kit_args(config)
    return kits, args


def kit_drift(config: AgyConfig, current_kit_names: List[str]) -> Tuple[List[str], List[str]]:
    """(added, removed) kit names between agy.yaml and what the sandbox was created with."""
    wanted = [Path(k).name for k in desired_kits(config)[0]]
    added = [k for k in wanted if k not in current_kit_names]
    removed = [k for k in current_kit_names if k not in wanted]
    return added, removed


def check_existing_sandbox_host_browser(config: AgyConfig, current_kit_names: List[str]) -> None:
    """Kits are fixed at creation, so an existing sandbox either has the chrome-host relay or not."""
    has_host_kit = KIT_CHROME_HOST in current_kit_names
    if config.host_browser.enabled and not has_host_kit:
        raise HostBrowserError(
            "This sandbox was created without host browser support. "
            "Add it without losing anything inside: agy-sandbox upgrade --host-browser"
        )
    if not config.host_browser.enabled and has_host_kit:
        print(
            "⚠️  This sandbox was created with --host-browser, so browser tools have no browser without it. "
            "Run 'agy-sandbox up --host-browser', or 'agy-sandbox upgrade' to drop it."
        )


def missing_proxy_secrets(config: AgyConfig) -> Set[str]:
    """In proxy-managed mode, the services the attached agents use that have no stored secret."""
    if config.auth_mode != AUTH_MODE_PROXY_MANAGED:
        return set()
    wanted: Set[str] = set()
    for spec in config.requested_agent_specs():
        wanted.update(spec.secret_services)
    return wanted - sandbox.stored_secret_services(config.sandbox_name)


def report_missing_secrets(config: AgyConfig) -> None:
    missing = missing_proxy_secrets(config)
    if not missing:
        return
    print("⚠️  auth_mode is proxy-managed but no sbx secret is stored for: " + ", ".join(sorted(missing)))
    for service in sorted(missing):
        print(f"   sbx secret set {service} --sandbox {config.sandbox_name}")
    print("   Agents using these services will have no credential until a secret is stored.")


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------
def resume(config: AgyConfig) -> int:
    name = config.sandbox_name
    current = sandbox.sandbox_kit_names(name)
    if KIT_CHROME_DEVTOOLS in config.all_kits():
        check_existing_sandbox_host_browser(config, current)
    added, removed = kit_drift(config, current)
    if added or removed:
        changes = (f"+{k}" for k in added), (f"-{k}" for k in removed)
        print(f"💡 agy.yaml kits differ from this sandbox ({' '.join([*changes[0], *changes[1]])}). "
              "Apply them without losing anything inside: agy-sandbox upgrade")
    sandbox.publish_ports(name, config.ports)
    if config.host_browser.enabled:
        host_browser_for(config).start()
    print(f"Resuming existing sandbox '{name}'...")
    return sandbox.attach(name, env=session_env(config))


def create(config: AgyConfig, rebuild: bool) -> int:
    name = config.sandbox_name
    if rebuild:
        sandbox.remove_sandbox(name)

    image = images.build_project_image(config)
    if rebuild or not sandbox.template_loaded(images.image_id(f"{image}:latest")):
        sandbox.load_template(image)
    else:
        print(f"Template image '{image}:latest' is already in the Docker Sandboxes store.")

    kits, args = desired_kits(config)
    report_missing_secrets(config)
    if config.host_browser.enabled:
        host_browser_for(config).start()

    print(f"Creating sandbox '{name}' ({config.auth_mode} logins)...")
    sandbox.create(
        name, f"{image}:latest", os.getcwd(), kits=kits, kit_args=args,
        env=session_env(config), ports=config.ports, clone=config.clone,
    )
    return sandbox.attach(name, env=session_env(config))


def up(config: AgyConfig, rebuild: bool = False) -> int:
    """Builds (if needed) and attaches. Returns the exit code of the attached sbx session."""
    if config.host_browser.enabled:
        validate_host_browser(config)
    sandbox.require_sbx()
    if sandbox.sandbox_exists(config.sandbox_name) and not rebuild:
        return resume(config)
    return create(config, rebuild)


def warn_missing_agent_binaries(config: AgyConfig) -> None:
    """
    Agent binaries come from the image layers, which an upgrade keeps as they were. An
    agent added to agy.yaml after creation gets its kit but not its binary; say so.
    """
    binaries = [spec.cli_binary for spec in config.requested_agent_specs() if spec.cli_binary]
    if not binaries:
        return
    checks = "; ".join(f"command -v {b} >/dev/null 2>&1 || echo {b}" for b in binaries)
    res = sandbox.exec_script(config.sandbox_name, checks, user="agent")
    missing = [b for b in (res.stdout or "").split() if b in binaries]
    if missing:
        print(f"⚠️  Not installed in this sandbox: {', '.join(missing)}. The kit is attached, but the binary "
              "comes from the image layer. Install it inside the sandbox, or bake it with 'agy-sandbox up --rebuild'.")


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------
def upgrade(config: AgyConfig) -> int:
    """
    Recreates the sandbox with agy.yaml's current kits, login mode, env and ports while
    keeping everything inside it. sbx stores a sandbox in three places and each is
    carried over explicitly:

      container filesystem  snapshotted with `sbx template save` and used as the new template,
                            so every package, tool and dotfile the agent installed survives
      kit volumes           copied out with `sbx cp` and back in (sbx deletes them on `rm`)
      clone-mode workspace  same, including its git history

    The snapshot is kept as a rollback point until the next upgrade. A new image
    (Dockerfile or build_args changes) cannot be merged into an existing filesystem;
    that is what `up --rebuild` is for.
    """
    if config.host_browser.enabled:
        validate_host_browser(config)
    sandbox.require_sbx()
    name = config.sandbox_name
    if not sandbox.sandbox_exists(name):
        raise sandbox.SandboxError(f"Sandbox '{name}' does not exist; create it with 'agy-sandbox up'.")

    info = sandbox.inspect_sandbox(name)
    workspace = str(info.get("workspace") or os.getcwd())
    clone = bool(info.get("source_repo_dir"))
    if clone != config.clone:
        print(f"⚠️  agy.yaml says clone: {config.clone} but the sandbox was created with clone: {clone}; "
              "clone mode is fixed at creation and is kept.")
    current_paths = [str(k) for k in info.get("kits") or []]
    current_names = [Path(k).name for k in current_paths]

    kits, args = desired_kits(config)
    added, removed = kit_drift(config, current_names)
    print(f"Upgrading sandbox '{name}' ({config.auth_mode} logins)")
    print(f"   kits: {' '.join(Path(k).name for k in kits) or '(none)'}")
    if added:
        print(f"   added:   {' '.join(added)}")
    if removed:
        print(f"   removed: {' '.join(removed)}")

    # 1. Carry out what `sbx rm` would delete: the clone workspace and every kit volume.
    carry: List[str] = [workspace] if clone else []
    for path in kit_volume_paths(current_paths + kits):
        if path not in carry:
            carry.append(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = Path(UPGRADE_STAGING_DIR).expanduser() / name / stamp
    staged: List[Tuple[str, str]] = []
    if carry:
        print("Carrying out the clone workspace and kit volumes..." if clone else "Carrying out kit volumes...")
        sandbox.exec_script(name, "true")  # a stopped sandbox is started so cp can read it
        for index, path in enumerate(carry):
            dest = staging / str(index)
            sandbox.copy_from_sandbox(name, path, str(dest))
            staged.append((path, str(dest / Path(path).name)))

    # 2. Snapshot the whole container filesystem, then replace the sandbox.
    tag = f"{config.image_name}:{UPGRADE_TAG_PREFIX}{stamp}"
    sandbox.stop_sandbox(name)
    sandbox.save_template(name, tag)
    sandbox.remove_sandbox(name)
    try:
        sandbox.create(
            name, tag, workspace, kits=kits, kit_args=args,
            env=session_env(config), ports=config.ports, clone=clone,
        )
    except sandbox.SandboxError:
        print(f"\n❌ The sandbox could not be recreated. Its snapshot is template '{tag}'"
              + (f" and its carried-out data is under {staging}" if staged else "") + ".")
        print(f"   Restore it with: sbx create --name {name} --template {tag} --pull never"
              + (" --clone" if clone else "") + f" shell {workspace}")
        raise

    # 3. Put the carried-out data back and hand it to the agent user.
    for path, src in staged:
        parent = str(Path(path).parent)
        sandbox.exec_script(name, f"mkdir -p '{path}' && find '{path}' -mindepth 1 -delete", user="root")
        sandbox.copy_to_sandbox(src, name, parent)
        sandbox.exec_script(name, f"chown -R agent:agent '{path}'", user="root")
    if staged:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            staging.parent.rmdir()
        except OSError:
            pass
        print("Restored the carried-out data.")
    warn_missing_agent_binaries(config)

    # 4. Keep this snapshot as the rollback point; older ones for this sandbox go.
    for image in sandbox.list_templates():
        repo, old_tag = str(image.get("repository", "")), str(image.get("tag", ""))
        if repo.endswith(f"/{config.image_name}") and old_tag.startswith(UPGRADE_TAG_PREFIX) and old_tag != tag.split(":")[1]:
            sandbox.remove_template(f"{config.image_name}:{old_tag}")
    print(f"Upgrade complete. Snapshot kept as '{tag}' for rollback.")

    report_missing_secrets(config)
    if config.host_browser.enabled:
        host_browser_for(config).start()
    return sandbox.attach(name, env=session_env(config))


# ---------------------------------------------------------------------------
# down
# ---------------------------------------------------------------------------
def down(config: AgyConfig) -> None:
    sandbox.require_sbx()
    name = config.sandbox_name
    if not sandbox.sandbox_exists(name):
        print(f"Sandbox '{name}' does not exist.")
    else:
        sandbox.remove_sandbox(name)
        print("Done.")
    host_browser_for(config).stop()
