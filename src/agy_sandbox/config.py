import os
import yaml
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RuntimeConfig:
    python: Optional[str] = None
    node: Optional[str] = None
    apt_packages: List[str] = field(default_factory=list)


@dataclass
class AgyConfig:
    profile: str = "default"
    project_name: str = "default_project"
    workspace_path: Optional[str] = None
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    setup_scripts: List[str] = field(default_factory=list)
    env: List[str] = field(default_factory=list)
    use_native_login: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "AgyConfig":
        runtime_data = data.get("runtime", {})
        runtime = RuntimeConfig(
            python=str(runtime_data.get("python"))
            if runtime_data.get("python")
            else None,
            node=str(runtime_data.get("node")) if runtime_data.get("node") else None,
            apt_packages=runtime_data.get("apt_packages", []),
        )
        return cls(
            profile=data.get("profile", "default"),
            project_name=data.get("project_name", "default_project"),
            workspace_path=data.get("workspace_path"),
            runtime=runtime,
            setup_scripts=data.get("setup_scripts", []),
            env=data.get("env", []),
            use_native_login=data.get("use_native_login", False),
        )


def load_config(path: str = "agy.yaml") -> AgyConfig:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Configuration file {path} not found.")

    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    return AgyConfig.from_dict(data)


def write_default_config(path: str = "agy.yaml") -> None:
    default_config = {
        "profile": "default",
        "project_name": os.path.basename(os.getcwd()),
        "runtime": {
            "python": "3.11",
            "node": "20",
            "apt_packages": ["build-essential"],
        },
        "setup_scripts": ["npm install", "pip install -r requirements.txt"],
        "env": ["ENVIRONMENT=development"],
        "use_native_login": False,
    }
    with open(path, "w") as f:
        yaml.dump(default_config, f, sort_keys=False)
