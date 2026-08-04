import os
import yaml
from dataclasses import dataclass, field
from typing import List, Optional



from .constants import (
    AGENT_AGY,
    AGENT_OMP,
    AGENT_SHELL,
    DEFAULT_CONFIG_FILE,
    DEFAULT_SBX_KIT_URL,
    KIT_OMP,
)


@dataclass
class SbxConfig:
    enabled: bool = False
    agent: str = AGENT_AGY
    clone: bool = False
    kits: List[str] = field(default_factory=list)


@dataclass
class AgyConfig:
    profile: str = "default"
    project_name: str = "default_project"
    workspace_path: Optional[str] = None
    build_args: dict = field(default_factory=dict)
    setup_scripts: List[str] = field(default_factory=list)
    env: List[str] = field(default_factory=list)
    use_native_login: bool = False
    sbx: SbxConfig = field(default_factory=SbxConfig)

    @property
    def sandbox_name(self) -> str:
        return f"agy-sandbox-{self.project_name}".replace("_", "-")

    @property
    def container_name(self) -> str:
        return f"agy-sandbox-container-{self.project_name}"

    @property
    def image_name(self) -> str:
        return f"agy-sandbox-{self.project_name}".replace("_", "-")

    @property
    def is_omp_requested(self) -> bool:
        return KIT_OMP in self.sbx.kits or self.sbx.agent == AGENT_OMP

    @classmethod
    def from_dict(cls, data: dict) -> "AgyConfig":
        build_args = data.get("build_args", {})

        # Backward compatibility for old configs containing "runtime"
        if "runtime" in data:
            runtime_data = data["runtime"]
            if "python" in runtime_data and "PYTHON_VERSION" not in build_args:
                build_args["PYTHON_VERSION"] = str(runtime_data["python"])
            if "node" in runtime_data and "NODE_VERSION" not in build_args:
                build_args["NODE_VERSION"] = str(runtime_data["node"])
            if "apt_packages" in runtime_data and "APT_PACKAGES" not in build_args:
                build_args["APT_PACKAGES"] = " ".join(runtime_data["apt_packages"])

        sbx_data = data.get("sbx", {})
        sbx = SbxConfig(
            enabled=sbx_data.get("enabled", False),
            agent=sbx_data.get("agent", AGENT_AGY),
            clone=sbx_data.get("clone", False),
            kits=sbx_data.get("kits", []),
        )
        return cls(
            profile=data.get("profile", "default"),
            project_name=data.get("project_name", "default_project"),
            workspace_path=data.get("workspace_path"),
            build_args=build_args,
            setup_scripts=data.get("setup_scripts", []),
            env=data.get("env", []),
            use_native_login=data.get("use_native_login", False),
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
    if config.workspace_path is not None:
        data["workspace_path"] = config.workspace_path

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, sort_keys=False)


def resolve_sbx_params(
    sbx: bool = False,
    omp: bool = False,
    clone: bool = False,
    agent: Optional[str] = None,
    kits: Optional[List[str]] = None,
) -> SbxConfig:
    sbx_enabled = sbx or omp or bool(kits) or (agent is not None)
    clone_enabled = clone or sbx_enabled

    resolved_agent = agent
    if not resolved_agent:
        resolved_agent = AGENT_SHELL if omp else AGENT_AGY

    resolved_kits = []
    if sbx_enabled:
        if resolved_agent == AGENT_AGY:
            resolved_kits.append(DEFAULT_SBX_KIT_URL)
        resolved_kits.append(".")

    if omp and KIT_OMP not in resolved_kits:
        resolved_kits.append(KIT_OMP)

    if kits:
        for kit in kits:
            if kit not in resolved_kits:
                resolved_kits.append(kit)

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
    omp: bool = False,
) -> None:
    sbx = resolve_sbx_params(
        sbx=sbx_enabled,
        omp=omp,
        clone=clone_enabled,
        agent=agent if agent != AGENT_AGY else None,
        kits=kits,
    )

    default_config = {
        "profile": "default",
        "project_name": os.path.basename(os.getcwd()),
        "build_args": {
            "PYTHON_VERSION": "3.11",
            "NODE_VERSION": "20",
            "RUST_VERSION": "stable",
            "MOJO_VERSION": "latest",
        },
        "setup_scripts": ["npm install", "pip install -r requirements.txt"],
        "env": ["ENVIRONMENT=development"],
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
