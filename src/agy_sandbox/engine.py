"""
Sandbox lifecycle: `up` and `down`. Orchestration only; the pieces live in
workload (the generated kit), compose (the plan), sandbox (sbx), guardian and
host_browser.
"""
from pathlib import Path
from typing import Optional, Set

from . import compose, console, sandbox, workload
from .compose import SandboxPlan
from .config import AgyConfig, new_sandbox_id, write_identity
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
    if drift.empty:
        return
    changes = [f"+{m.name}" for m in drift.added] + drift.reasons()
    console.hint(f"agy.yaml differs from this sandbox ({'; '.join(changes)}). Apply it with: {drift.remedy()}")


def desired_plan(config: AgyConfig, cwd: Path, *, write: bool = True) -> SandboxPlan:
    """
    The plan agy.yaml asks for. With `write`, the workload kit directory is written so sbx
    can build it; without, it is only rendered (for drift reports), so the directory on
    disk keeps matching the sandbox that was built from it.
    """
    ref = workload.write(config, cwd) if write else workload.render(config, cwd).ref
    return compose.plan(config, workspace=str(cwd), workload=ref)


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------
def _session(name: str, env, attach: bool) -> int:
    """Attaches to the sandbox, or says how to when the caller only wanted the work done."""
    if not attach:
        console.hint(f"Sandbox '{name}' is ready. Attach with: agy-sandbox up")
        return 0
    return sandbox.attach(name, env=env)


def resume(config: AgyConfig, cwd: Path, *, attach: bool = True) -> int:
    name = config.sandbox_name
    saved = compose.load_plan(name)
    if saved is None:
        console.warn(f"Sandbox '{name}' was created by an older agy-sandbox; 'upgrade' cannot apply agy.yaml "
                     "changes to it. Use 'agy-sandbox recreate' (keeps its contents) or 'up --rebuild'.")
    else:
        report_drift(saved, desired_plan(config, cwd, write=False))
        if config.host_browser.enabled and KIT_CHROME_HOST not in saved.mixin_names():
            console.warn("This sandbox was created without host browser support; run 'agy-sandbox upgrade --host-browser' to add it.")
    sandbox.publish_ports(name, config.ports)
    if config.host_browser.enabled:
        host_browser_for(config).start()
    console.step(f"Resuming sandbox '{name}'...")
    return _session(name, config.env, attach)


def create(config: AgyConfig, cwd: Path, *, attach: bool = True) -> int:
    plan = desired_plan(config, cwd)
    report_missing_secrets(config)
    console.step(f"Creating sandbox '{plan.name}' ({config.auth_mode} logins); sbx builds the project image first...")
    sandbox.create(plan)
    compose.save_plan(plan)
    if config.host_browser.enabled:
        host_browser_for(config).start()
    return _session(plan.name, plan.env, attach)


def up(config: AgyConfig, *, rebuild: bool = False, cwd: Path, attach: bool = True,
       config_path: Optional[str] = None) -> int:
    """
    Creates or resumes the sandbox and attaches. Returns the exit code of the attached session
    (0 without attach). A project without a sandbox id keeps using its pre-identity sandbox while
    that exists; the moment one has to be created it is given an id, recorded in `config_path`.
    """
    compose.validate_host_browser(config)
    sandbox.require()
    name = config.sandbox_name
    if sandbox.exists(name):
        if not rebuild:
            return resume(config, cwd, attach=attach)
        sandbox.remove(name)
        workload.delete(name)
    if config.sandbox_id is None:
        config.sandbox_id = new_sandbox_id()
        config.sandbox_version = 1
        if config_path:
            write_identity(config, config_path)
            console.step(f"Gave this project a sandbox id (recorded in {config_path}); its sandboxes are named '{config.sandbox_name}' and up.")
    return create(config, cwd, attach=attach)


# ---------------------------------------------------------------------------
# down
# ---------------------------------------------------------------------------
def down(config: AgyConfig) -> None:
    name = config.sandbox_name
    try:
        sandbox.require()
        if not sandbox.exists(name):
            console.info(f"Sandbox '{name}' does not exist.")
        else:
            sandbox.remove(name)
            console.step("Done.")
        workload.delete(name)
    finally:
        host_browser_for(config).stop()
