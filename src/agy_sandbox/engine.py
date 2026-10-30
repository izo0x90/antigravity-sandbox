"""
Sandbox lifecycle: `up` builds the project image, loads it into sbx and creates
or resumes the sandbox; `down` removes it. Everything agent-specific comes
from the kits; everything login-specific from `auth_mode`.
"""
from typing import List, Set

from . import images, sandbox
from .config import AgyConfig
from .constants import AUTH_MODE_PROXY_MANAGED, KIT_CHROME_DEVTOOLS, KIT_CHROME_HOST
from .host_browser import HostBrowser, HostBrowserError
from .kits import kit_args, run_kits


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


def check_existing_sandbox_host_browser(config: AgyConfig) -> None:
    """Kits are fixed at creation, so an existing sandbox either has the chrome-host relay or not."""
    has_host_kit = KIT_CHROME_HOST in sandbox.sandbox_kit_names(config.sandbox_name)
    if config.host_browser.enabled and not has_host_kit:
        raise HostBrowserError(
            "This sandbox was created without host browser support. "
            "Recreate it with: agy-sandbox up --host-browser --rebuild"
        )
    if not config.host_browser.enabled and has_host_kit:
        print(
            "⚠️  This sandbox was created with --host-browser, so browser tools have no browser without it. "
            "Run 'agy-sandbox up --host-browser', or 'agy-sandbox up --rebuild' to switch back."
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


def resume(config: AgyConfig) -> int:
    name = config.sandbox_name
    if KIT_CHROME_DEVTOOLS in config.all_kits():
        check_existing_sandbox_host_browser(config)
    sandbox.publish_ports(name, config.ports)
    if config.host_browser.enabled:
        host_browser_for(config).start()
    print(f"Resuming existing sandbox '{name}'...")
    print("💡 To rebuild with an updated image or kits, run with '--rebuild'.")
    return sandbox.attach(name, env=config.env)


def create(config: AgyConfig, rebuild: bool) -> int:
    name = config.sandbox_name
    if rebuild:
        sandbox.remove_sandbox(name)

    image = images.build_project_image(config)
    if rebuild or not sandbox.template_loaded(images.image_id(f"{image}:latest")):
        sandbox.load_template(image)
    else:
        print(f"Template image '{image}:latest' is already in the Docker Sandboxes store.")

    extra_kits = [KIT_CHROME_HOST] if config.host_browser.enabled else []
    kits = run_kits(config, extra_kits=extra_kits)
    args = kit_args(config, kits)
    if config.host_browser.enabled:
        args += host_browser_kit_args(config)

    report_missing_secrets(config)
    if config.host_browser.enabled:
        host_browser_for(config).start()

    print(f"Creating sandbox '{name}' ({config.auth_mode} logins)...")
    return sandbox.create_and_attach(
        name, image, kits=kits, kit_args=args, env=config.env, ports=config.ports, clone=config.clone
    )


def up(config: AgyConfig, rebuild: bool = False) -> int:
    """Builds (if needed) and attaches. Returns the exit code of the attached sbx session."""
    if config.host_browser.enabled:
        validate_host_browser(config)
    sandbox.require_sbx()
    if sandbox.sandbox_exists(config.sandbox_name) and not rebuild:
        return resume(config)
    return create(config, rebuild)


def down(config: AgyConfig) -> None:
    sandbox.require_sbx()
    name = config.sandbox_name
    if not sandbox.sandbox_exists(name):
        print(f"Sandbox '{name}' does not exist.")
    else:
        sandbox.remove_sandbox(name)
        print("Done.")
    host_browser_for(config).stop()
