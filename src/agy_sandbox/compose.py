"""
From agy.yaml to the exact sbx composition: which workload, which mixins in
which order, which kit arguments. The result is saved next to the sandbox so
later runs can tell what changed. Pure: no sbx calls.
"""
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from . import __version__, paths
from .config import AgyConfig
from .constants import AUTH_MODE_PROXY_MANAGED, KIT_CHROME_DEVTOOLS, KIT_CHROME_HOST, PLAN_SCHEMA_VERSION
from .errors import ConfigError
from .kits import KitRef, credential_services, credentials_kit, load_descriptor, resolve
from .workload import WorkloadRef


@dataclass(frozen=True)
class SandboxPlan:
    name: str
    workspace: str
    clone: bool
    auth_mode: str
    workload: WorkloadRef
    mixins: Tuple[KitRef, ...]
    kit_args: Tuple[str, ...]
    env: Tuple[str, ...]
    ports: Tuple[str, ...]
    agy_version: str = __version__

    def to_dict(self) -> dict:
        data = asdict(self)
        data["mixins"] = [asdict(m) for m in self.mixins]
        data["workload"] = asdict(self.workload)
        return {"schema": PLAN_SCHEMA_VERSION, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **data}

    @classmethod
    def from_dict(cls, data: dict) -> "SandboxPlan":
        return cls(
            name=str(data["name"]),
            workspace=str(data["workspace"]),
            clone=bool(data.get("clone", False)),
            auth_mode=str(data["auth_mode"]),
            workload=WorkloadRef(**data["workload"]),
            mixins=tuple(KitRef(**m) for m in data.get("mixins") or []),
            kit_args=tuple(data.get("kit_args") or []),
            env=tuple(data.get("env") or []),
            ports=tuple(data.get("ports") or []),
            agy_version=str(data.get("agy_version") or ""),
        )

    def mixin_names(self) -> List[str]:
        return [m.name for m in self.mixins]


def host_browser_kit_args(config: AgyConfig) -> List[str]:
    return [f"{KIT_CHROME_HOST}.host_port={config.host_browser.port}"]


def validate_host_browser(config: AgyConfig) -> None:
    if config.host_browser.enabled and KIT_CHROME_DEVTOOLS not in config.all_kits():
        raise ConfigError(
            f"--host-browser needs the '{KIT_CHROME_DEVTOOLS}' kit, which registers the browser MCP server.",
            hint=f"Run: agy-sandbox kits add {KIT_CHROME_DEVTOOLS}",
        )


def mixins_for(config: AgyConfig) -> Tuple[KitRef, ...]:
    """
    Mixin order: the primary agent's kit, the configured kits, each followed by its
    `-credentials` kit in proxy-managed mode, then chrome-host when the host browser is on.

    sbx allows one owner per credential (service, phase) in a composition, so two
    agents that both declare a provider (claude and opencode both use `anthropic`)
    cannot share a proxy-managed sandbox; that is reported here, before sbx is called.
    """
    chosen: List[KitRef] = []
    owners: Dict[Tuple[str, str], str] = {}

    def add(ref: Optional[KitRef]) -> None:
        if ref and all(ref.source != c.source for c in chosen):
            chosen.append(ref)

    def add_credentials(kit: KitRef) -> None:
        creds = credentials_kit(kit)
        if not creds:
            return
        for service, phase in credential_services(load_descriptor(Path(creds.source))):
            owner = owners.setdefault((service, phase), creds.name)
            if owner != creds.name:
                raise ConfigError(
                    f"auth_mode: proxy-managed cannot attach both {owner} and {creds.name}: "
                    f"both declare the '{service}' credential, and sbx allows one owner per service.",
                    hint="Keep one of those agents in this sandbox, or use auth_mode: sandbox-local.",
                )
        add(creds)

    for name in config.all_kits():
        kit = resolve(name)
        add(kit)
        if config.auth_mode == AUTH_MODE_PROXY_MANAGED:
            add_credentials(kit)
    if config.host_browser.enabled:
        add(resolve(KIT_CHROME_HOST))
    return tuple(chosen)


def kit_args_for(config: AgyConfig) -> Tuple[str, ...]:
    """agy.yaml kit_args, plus chrome-host's port when the host browser is on (host_browser.port wins)."""
    port_arg = f"{KIT_CHROME_HOST}.host_port"
    args = [f"{key}={value}" for key, value in config.kit_args.items()
            if not (config.host_browser.enabled and key == port_arg)]
    if config.host_browser.enabled:
        args += host_browser_kit_args(config)
    return tuple(args)


def plan(config: AgyConfig, *, workspace: str, workload: WorkloadRef) -> SandboxPlan:
    validate_host_browser(config)
    return SandboxPlan(
        name=config.sandbox_name,
        workspace=os.path.abspath(workspace),
        clone=config.clone,
        auth_mode=config.auth_mode,
        workload=workload,
        mixins=mixins_for(config),
        kit_args=kit_args_for(config),
        env=tuple(config.env),
        ports=tuple(config.ports),
    )


# ---------------------------------------------------------------------------
# drift: what changed between the plan a sandbox was built with and the plan agy.yaml asks for
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Drift:
    added: Tuple[KitRef, ...]
    removed: Tuple[str, ...]
    kit_args_changed: Tuple[str, ...]
    workload_changed: bool
    clone_changed: bool
    auth_mode_changed: bool

    @property
    def in_place(self) -> bool:
        """
        True when sbx can apply everything to the existing sandbox: additions only. A
        login-mode change is excluded even though it only adds or removes credential
        kits, because the agent kits seed their credential-dependent config at install.
        """
        return not (self.removed or self.kit_args_changed or self.workload_changed or self.clone_changed
                    or self.auth_mode_changed)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.kit_args_changed or self.workload_changed
                    or self.clone_changed or self.auth_mode_changed)

    def reasons(self) -> List[str]:
        out: List[str] = []
        if self.auth_mode_changed:
            out.append("login mode (auth_mode) changed; agent kits seed their credential config when the sandbox is created")
        for name in self.removed:
            out.append(f"kit removed: {name}")
        for name in self.kit_args_changed:
            out.append(f"kit arguments changed: {name}")
        if self.workload_changed:
            out.append("workload changed (Dockerfile.agy, build_args or setup_scripts)")
        if self.clone_changed:
            out.append("clone mode changed (fixed at creation; use 'agy-sandbox up --rebuild')")
        return out


def kit_args_for_kit(kit_args: Sequence[str], kit: str) -> FrozenSet[str]:
    """Arguments that apply to one kit: `<kit>.name=value` entries plus unscoped `name=value` ones."""
    chosen = set()
    for arg in kit_args:
        key = arg.split("=", 1)[0]
        if "." not in key or key.startswith(kit + "."):
            chosen.add(arg)
    return frozenset(chosen)


def diff(saved: SandboxPlan, wanted: SandboxPlan) -> Drift:
    saved_sources = {m.source for m in saved.mixins}
    wanted_sources = {m.source for m in wanted.mixins}
    added = tuple(m for m in wanted.mixins if m.source not in saved_sources)
    removed = tuple(m.name for m in saved.mixins if m.source not in wanted_sources)
    common = [m.name for m in wanted.mixins if m.source in saved_sources]
    changed = tuple(sorted(k for k in common if kit_args_for_kit(saved.kit_args, k) != kit_args_for_kit(wanted.kit_args, k)))
    return Drift(
        added=added,
        removed=removed,
        kit_args_changed=changed,
        workload_changed=saved.workload.fingerprint != wanted.workload.fingerprint,
        clone_changed=saved.clone != wanted.clone,
        auth_mode_changed=saved.auth_mode != wanted.auth_mode,
    )


# ---------------------------------------------------------------------------
# persistence: the plan a sandbox was created or last upgraded with
# ---------------------------------------------------------------------------
def save_plan(sandbox_plan: SandboxPlan) -> None:
    path = paths.plan_path(sandbox_plan.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sandbox_plan.to_dict(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_plan(name: str) -> Optional[SandboxPlan]:
    path = paths.plan_path(name)
    if not path.is_file():
        return None
    try:
        data: Dict = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"Saved sandbox plan {path} is unreadable: {e}", hint="Delete it and run 'agy-sandbox up --rebuild'.")
    if not isinstance(data, dict):
        raise ConfigError(f"Saved sandbox plan {path} is not a JSON object.", hint="Delete it and run 'agy-sandbox up --rebuild'.")
    schema = int(data.get("schema") or 0)
    if schema > PLAN_SCHEMA_VERSION:
        raise ConfigError(
            f"Sandbox '{name}' was created by a newer agy-sandbox (plan schema {schema}).",
            hint="Upgrade agy-sandbox: uv tool install -e . --force",
        )
    try:
        return SandboxPlan.from_dict(data)
    except (KeyError, TypeError) as e:
        raise ConfigError(f"Saved sandbox plan {path} is incomplete: {e}", hint="Delete it and run 'agy-sandbox up --rebuild'.")


def delete_plan(name: str) -> None:
    path = paths.plan_path(name)
    if path.is_file():
        path.unlink()
    try:
        path.parent.rmdir()
    except OSError:
        pass
