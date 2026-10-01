from dataclasses import dataclass, field
import os
from typing import Dict, List, Optional
import yaml

from .constants import (
    AGENT_AGY,
    AGENT_OMP,
    AGENT_PRIME_AGENT,
    AGENT_SPECS,
    AgentSpec,
    DEFAULT_BUILD_ARGS,
    DEFAULT_CONFIG_FILE,
    DEFAULT_ENV_VARS,
    DEFAULT_HOST_BROWSER_PORT,
    DEFAULT_SETUP_SCRIPTS,
    KIT_OMP,
    KIT_PRIME_AGENT,
)
from .naming import SandboxNamingResolver


@dataclass
class HostBrowserConfig:
    enabled: bool = False
    port: int = DEFAULT_HOST_BROWSER_PORT
    headless: bool = False

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "HostBrowserConfig":
        data = data or {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            port=int(data.get("port") or DEFAULT_HOST_BROWSER_PORT),
            headless=bool(data.get("headless", False)),
        )

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "port": self.port, "headless": self.headless}


@dataclass
class SbxConfig:
    enabled: bool = False
    agent: str = AGENT_AGY
    clone: bool = False
    kits: List[str] = field(default_factory=list)
    host_browser: HostBrowserConfig = field(default_factory=HostBrowserConfig)


@dataclass
class AgyConfig:
    profile: str = "default"
    project_name: str = "default_project"
    agent: str = AGENT_AGY
    auth_mode: str = "sbx_persistent"
    workspace_path: Optional[str] = None
    build_args: Dict[str, str] = field(default_factory=dict)
    setup_scripts: List[str] = field(default_factory=list)
    env: List[str] = field(default_factory=list)
    use_native_login: bool = False
    sbx: SbxConfig = field(default_factory=SbxConfig)

    @property
    def sandbox_name(self) -> str:
        return SandboxNamingResolver.resolve_sandbox_name(self.project_name, self.profile)

    @property
    def legacy_sandbox_name(self) -> str:
        return SandboxNamingResolver.resolve_legacy_sandbox_name(self.project_name)

    @property
    def container_name(self) -> str:
        return SandboxNamingResolver.resolve_container_name(self.project_name, self.profile)

    @property
    def image_name(self) -> str:
        return SandboxNamingResolver.resolve_image_name(self.project_name)

    def get_requested_agent_specs(self) -> List[AgentSpec]:
        """
        Dynamically collects and resolves all requested agent specifications from
        self.agent, self.sbx.agent, and self.sbx.kits in insertion order without duplicates.
        """
        requested_keys: List[str] = []

        def _add_key(key: str) -> None:
            if not key:
                return

            norm_key = os.path.basename(key.rstrip("/\\")) if ("/" in key or "\\" in key) else key

            if key in AGENT_SPECS and key not in requested_keys:
                requested_keys.append(key)
            elif norm_key in AGENT_SPECS and norm_key not in requested_keys:
                requested_keys.append(norm_key)
            else:
                for spec_key, spec in AGENT_SPECS.items():
                    if spec.kit_ref and (spec.kit_ref == key or spec.kit_ref == norm_key) and spec_key not in requested_keys:
                        requested_keys.append(spec_key)

        _add_key(self.agent)
        _add_key(self.sbx.agent)
        for kit in self.sbx.kits:
            _add_key(kit)

        return [AGENT_SPECS[k] for k in requested_keys]

    @property
    def is_omp_requested(self) -> bool:
        return any(spec.identifier == AGENT_OMP for spec in self.get_requested_agent_specs())

    @property
    def is_prime_requested(self) -> bool:
        return any(spec.identifier == AGENT_PRIME_AGENT for spec in self.get_requested_agent_specs())

    @property
    def agent_spec(self) -> AgentSpec:
        agent_key = self.sbx.agent if (self.sbx.agent in AGENT_SPECS and self.sbx.agent != AGENT_AGY) else self.agent
        return AGENT_SPECS.get(agent_key, AGENT_SPECS[AGENT_AGY])

    @classmethod
    def from_dict(cls, data: dict) -> "AgyConfig":
        data = data or {}
        build_args = data.get("build_args") or {}

        # Backward compatibility for old configs containing "runtime"
        if "runtime" in data and isinstance(data["runtime"], dict):
            runtime_data = data["runtime"]
            if "python" in runtime_data and "PYTHON_VERSION" not in build_args:
                build_args["PYTHON_VERSION"] = str(runtime_data["python"])
            if "node" in runtime_data and "NODE_VERSION" not in build_args:
                build_args["NODE_VERSION"] = str(runtime_data["node"])
            if "apt_packages" in runtime_data and "APT_PACKAGES" not in build_args:
                build_args["APT_PACKAGES"] = " ".join(runtime_data["apt_packages"])

        sbx_data = data.get("sbx")
        if isinstance(sbx_data, dict):
            sbx_dict = sbx_data
            sbx_enabled = sbx_dict.get("enabled", False)
        elif isinstance(sbx_data, bool):
            sbx_dict = {}
            sbx_enabled = sbx_data
        else:
            sbx_dict = {}
            sbx_enabled = False

        raw_agent = data.get("agent") or sbx_dict.get("agent") or AGENT_AGY
        raw_auth_mode = data.get("auth_mode") or sbx_dict.get("auth_mode") or "sbx_persistent"

        if raw_agent != AGENT_AGY:
            sbx_enabled = True

        sbx_kits = sbx_dict.get("kits") or []

        sbx = SbxConfig(
            enabled=sbx_enabled,
            agent=raw_agent,
            clone=sbx_dict.get("clone", False),
            kits=sbx_kits,
            host_browser=HostBrowserConfig.from_dict(sbx_dict.get("host_browser")),
        )
        return cls(
            profile=data.get("profile") or "default",
            project_name=data.get("project_name") or "default_project",
            agent=raw_agent,
            auth_mode=raw_auth_mode,
            workspace_path=data.get("workspace_path"),
            build_args=build_args,
            setup_scripts=data.get("setup_scripts") or [],
            env=data.get("env") or [],
            use_native_login=data.get("use_native_login") or False,
            sbx=sbx,
        )


def load_config(path: str = DEFAULT_CONFIG_FILE) -> AgyConfig:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Configuration file {path} not found.")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return AgyConfig.from_dict(data)


def save_config(config: AgyConfig, path: str = DEFAULT_CONFIG_FILE) -> None:
    data = {
        "profile": config.profile,
        "project_name": config.project_name,
        "agent": config.agent,
        "auth_mode": config.auth_mode,
        "build_args": config.build_args,
        "setup_scripts": config.setup_scripts,
        "env": config.env,
        "use_native_login": config.use_native_login,
        "sbx": {
            "enabled": config.sbx.enabled,
            "agent": config.sbx.agent,
            "clone": config.sbx.clone,
            "kits": config.sbx.kits,
        },
    }
    # Only persist host_browser once it differs from the defaults, to keep generated configs minimal
    if config.sbx.host_browser != HostBrowserConfig():
        data["sbx"]["host_browser"] = config.sbx.host_browser.to_dict()
    if config.workspace_path is not None:
        data["workspace_path"] = config.workspace_path

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, sort_keys=False)


def resolve_sbx_params(
    sbx: bool = False,
    omp: bool = False,
    clone: bool = False,
    agent: Optional[str] = None,
    additional_agents: Optional[List[str]] = None,
    kits: Optional[List[str]] = None,
) -> SbxConfig:
    resolved_agent = agent or (AGENT_OMP if omp else AGENT_AGY)
    sbx_enabled = sbx or omp or bool(kits) or bool(additional_agents) or (resolved_agent != AGENT_AGY)
    clone_enabled = clone or sbx_enabled

    resolved_kits = []
    if sbx_enabled:
        if resolved_agent in AGENT_SPECS:
            kit_ref = AGENT_SPECS[resolved_agent].kit_ref
            if kit_ref and kit_ref not in resolved_kits:
                resolved_kits.append(kit_ref)

    if additional_agents:
        for add_agent in additional_agents:
            if add_agent in AGENT_SPECS:
                kit_ref = AGENT_SPECS[add_agent].kit_ref
                if kit_ref and kit_ref not in resolved_kits:
                    resolved_kits.append(kit_ref)
            elif add_agent not in resolved_kits:
                resolved_kits.append(add_agent)

    if kits:
        for kit in kits:
            if kit and kit != "." and kit not in resolved_kits:
                resolved_kits.append(kit)

    resolved_kits = [k for k in resolved_kits if k != "."]

    return SbxConfig(
        enabled=sbx_enabled,
        agent=resolved_agent,
        clone=clone_enabled,
        kits=resolved_kits,
    )


def write_default_config(
    path: str = DEFAULT_CONFIG_FILE,
    sbx_enabled: bool = False,
    clone_enabled: bool = False,
    kits: Optional[List[str]] = None,
    agent: str = AGENT_AGY,
    additional_agents: Optional[List[str]] = None,
    omp: bool = False,
) -> None:
    sbx = resolve_sbx_params(
        sbx=sbx_enabled,
        omp=omp,
        clone=clone_enabled,
        agent=agent,
        additional_agents=additional_agents,
        kits=kits,
    )

    default_config = {
        "profile": "default",
        "project_name": os.path.basename(os.getcwd()),
        "agent": sbx.agent,
        "auth_mode": "sbx_persistent",
        "build_args": dict(DEFAULT_BUILD_ARGS),
        "setup_scripts": list(DEFAULT_SETUP_SCRIPTS),
        "env": list(DEFAULT_ENV_VARS),
        "use_native_login": False,
        "sbx": {
            "enabled": sbx.enabled,
            "agent": sbx.agent,
            "clone": sbx.clone,
            "kits": sbx.kits,
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(default_config, f, sort_keys=False)
