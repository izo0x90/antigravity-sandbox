"""
Sandbox lifecycle: `up` and `down`. Orchestration only; the pieces live in
workload (the generated kit), compose (the plan), sandbox (sbx), guardian and
host_browser.
"""
from pathlib import Path
from typing import Set

from . import compose, console, sandbox, workload
from .compose import SandboxPlan
from .config import AgyConfig
from .constants import AUTH_MODE_PROXY_MANAGED, KIT_CHROME_HOST
from .host_browser import HostBrowser


def host_browser_for(config: AgyConfig) -> HostBrowser:
    settings = config.host_browser
    return HostBrowser.for_sandbox(config.sandbox_name, settings.port, settings.headless)


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
    console.warn("auth_mode is proxy-managed but no sbx secret is stored for: " + ", ".join(sorted(missing)))
    for service in sorted(missing):
        console.info(f"   sbx secret set {service} --sandbox {config.sandbox_name}")
    console.info("   Agents using these services will have no credential until a secret is stored.")


def report_drift(saved: SandboxPlan, wanted: SandboxPlan) -> None:
    """On resume: say when agy.yaml no longer matches what the sandbox was built with."""
    drift = compose.diff(saved, wanted)
    changes = [f"+{m.name}" for m in drift.added] + [f"-{name}" for name in drift.removed]
    if drift.workload_changed:
        changes.append("workload (Dockerfile.agy / build_args / setup_scripts)")
    if drift.kit_args_changed:
        changes.append("kit_args for " + ", ".join(drift.kit_args_changed))
    if not changes:
        return
    command = "agy-sandbox upgrade" if drift.in_place else "agy-sandbox recreate"
    console.hint(f"agy.yaml differs from this sandbox ({' '.join(changes)}). Apply it without losing anything inside: {command}")


def desired_plan(config: AgyConfig, cwd: Path) -> SandboxPlan:
    """Writes the workload kit and composes the plan agy.yaml asks for."""
    ref = workload.write(config, cwd)
    return compose.plan(config, workspace=str(cwd), workload=ref)


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------
def resume(config: AgyConfig, cwd: Path) -> int:
    name = config.sandbox_name
    saved = compose.load_plan(name)
    if saved is None:
        console.warn(f"Sandbox '{name}' was created by an older agy-sandbox; 'upgrade' cannot apply agy.yaml "
                     "changes to it. Use 'agy-sandbox recreate' (keeps its contents) or 'up --rebuild'.")
    else:
        report_drift(saved, desired_plan(config, cwd))
        if config.host_browser.enabled and KIT_CHROME_HOST not in saved.mixin_names():
            console.warn("This sandbox was created without host browser support; run 'agy-sandbox upgrade --host-browser' to add it.")
    sandbox.publish_ports(name, config.ports)
    if config.host_browser.enabled:
        host_browser_for(config).start()
    console.step(f"Resuming sandbox '{name}'...")
    return sandbox.attach(name, env=config.env)


def create(config: AgyConfig, cwd: Path) -> int:
    plan = desired_plan(config, cwd)
    report_missing_secrets(config)
    if config.host_browser.enabled:
        host_browser_for(config).start()
    console.step(f"Creating sandbox '{plan.name}' ({config.auth_mode} logins); sbx builds the project image first...")
    sandbox.create(plan)
    compose.save_plan(plan)
    return sandbox.attach(plan.name, env=plan.env)


def up(config: AgyConfig, *, rebuild: bool = False, cwd: Path) -> int:
    """Creates or resumes the sandbox and attaches. Returns the exit code of the attached session."""
    compose.validate_host_browser(config)
    sandbox.require()
    name = config.sandbox_name
    if sandbox.exists(name):
        if not rebuild:
            return resume(config, cwd)
        sandbox.remove(name)
        compose.delete_plan(name)
    return create(config, cwd)


# ---------------------------------------------------------------------------
# down
# ---------------------------------------------------------------------------
def down(config: AgyConfig) -> None:
    sandbox.require()
    name = config.sandbox_name
    if not sandbox.exists(name):
        console.info(f"Sandbox '{name}' does not exist.")
    else:
        sandbox.remove(name)
        console.step("Done.")
    compose.delete_plan(name)
    host_browser_for(config).stop()
