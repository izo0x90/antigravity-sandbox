"""
agy.yaml: one flat document describing a sandbox.

    profile: default
    project_name: my_app
    sandbox_id: 3f9a2c1e...    # written by agy-sandbox on the first `up`; identifies this project's sandboxes
    sandbox_version: 1         # bumped by `recreate --next`; which of them agy.yaml points at
    agent: claude                 # primary agent; its kit is always attached
    kits: [claude, opencode]      # agent kits and mixin kits: bundled names, local dirs or remote refs
    auth_mode: sandbox-local      # or proxy-managed
    clone: true                   # work on a private in-VM clone instead of a bind mount
    ports: ["3000:3000"]          # published to the host
    host_browser: {enabled, port, headless}   # only written when non-default
    kit_args: {chrome-host.host_port: "9222"} # passed through as `--kit-arg`
    base_image: docker.io/sbx/shell-image:latest   # only written when non-default
    build_args: {PYTHON_VERSION: "3.12"}      # ARGs of Dockerfile.agy / the default Dockerfile
    setup_scripts: [uv sync]      # run once inside the VM when the sandbox is created
    env: [ENVIRONMENT=development]

Pre-0.5 files nested most of this under `sbx:` and carried a Docker-only
runtime; `AgyConfig.from_dict` still reads them and records what changed in
`migration_notes` so the CLI can tell the user how to update.
"""
from dataclasses import dataclass, field, replace
import os
import re
from typing import Dict, List, Optional, Tuple
import uuid

import yaml

from .agents import AGENT_AGY, AGENT_SPECS, AgentSpec
from .constants import (
    AUTH_MODE_SANDBOX_LOCAL,
    AUTH_MODES,
    DEFAULT_BASE_IMAGE,
    DEFAULT_BUILD_ARGS,
    DEFAULT_CONFIG_FILE,
    DEFAULT_ENV_VARS,
    DEFAULT_HOST_BROWSER_PORT,
    DEFAULT_SETUP_SCRIPTS,
    LEGACY_AUTH_MODES,
    slugify_project_name,
)
from .errors import ConfigError


SANDBOX_NAME_PREFIX = "agy-sandbox-"
SANDBOX_ID_CHARS = 20          # of the 32-hex id, in the name: 80 bits, far beyond any one machine's sandboxes
_SBX_NAME_MAX = 63             # sbx's limit on a sandbox name
_VERSION_WIDTH = 3             # room reserved for the version digits, so the label never shifts between versions
_SANDBOX_ID = re.compile(r"[0-9a-f]{32}")


def new_sandbox_id() -> str:
    """A fresh identity for a project's sandboxes: a random UUID4, as 32 hex characters."""
    return uuid.uuid4().hex


def sandbox_name(project_name: str, profile: str = "default", sandbox_id: Optional[str] = None, version: int = 1) -> str:
    """
    With an identity: agy-sandbox-<label>-<20 hex of the id>-v<version>. The label (project
    slug, plus --<profile> when not default) is only there so `sbx ls` reads well; the id is
    what makes the name unique, so the label is cut to fit sbx's 63-character limit.
    Without one: agy-sandbox-<label>, the name every sandbox had before identities existed.
    """
    project = slugify_project_name(project_name)
    prof = slugify_project_name(profile)
    label = project if prof == "default" else f"{project}--{prof}"
    if sandbox_id is None:
        return f"{SANDBOX_NAME_PREFIX}{label}"
    if version >= 10 ** _VERSION_WIDTH:
        raise ConfigError(f"sandbox_version must be below {10 ** _VERSION_WIDTH} (got {version}).")
    tail = f"-{sandbox_id[:SANDBOX_ID_CHARS]}-v"
    room = _SBX_NAME_MAX - len(SANDBOX_NAME_PREFIX) - len(tail) - _VERSION_WIDTH
    return f"{SANDBOX_NAME_PREFIX}{label[:room].rstrip('-')}{tail}{version}"


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
class AgyConfig:
    profile: str = "default"
    project_name: str = "default_project"
    sandbox_id: Optional[str] = None      # None until the first sandbox is created (or for a pre-identity file)
    sandbox_version: int = 1
    agent: str = AGENT_AGY
    kits: List[str] = field(default_factory=list)
    auth_mode: str = AUTH_MODE_SANDBOX_LOCAL
    clone: bool = False
    ports: List[str] = field(default_factory=list)
    host_browser: HostBrowserConfig = field(default_factory=HostBrowserConfig)
    kit_args: Dict[str, str] = field(default_factory=dict)
    base_image: str = DEFAULT_BASE_IMAGE
    build_args: Dict[str, str] = field(default_factory=dict)
    setup_scripts: List[str] = field(default_factory=list)
    env: List[str] = field(default_factory=list)
    # What `from_dict` had to translate from a pre-0.5 file; never saved
    migration_notes: List[str] = field(default_factory=list, compare=False, repr=False)

    @property
    def sandbox_name(self) -> str:
        """The sandbox agy.yaml points at: the current version under this identity, or the pre-identity name."""
        return sandbox_name(self.project_name, self.profile, self.sandbox_id, self.sandbox_version)

    @property
    def legacy_sandbox_name(self) -> str:
        """The name this project's sandbox had before identities (agy-sandbox 0.5 and early 0.6)."""
        return sandbox_name(self.project_name, self.profile)

    def with_version(self, version: int) -> "AgyConfig":
        return replace(self, sandbox_version=version)

    def next_version(self) -> "AgyConfig":
        """
        A copy pointing at the version after this one. A file without an identity gets one
        here, and its next version is 2: the pre-identity sandbox it has today counts as the first.
        """
        if self.sandbox_id is None:
            return replace(self, sandbox_id=new_sandbox_id(), sandbox_version=2)
        return replace(self, sandbox_version=self.sandbox_version + 1)

    @property
    def agent_spec(self) -> AgentSpec:
        return AGENT_SPECS.get(self.agent, AGENT_SPECS[AGENT_AGY])

    def all_kits(self) -> List[str]:
        """The primary agent's kit first, then every configured kit, without duplicates."""
        kits: List[str] = []
        if self.agent_spec.has_kit:
            kits.append(self.agent_spec.identifier)
        for kit in self.kits:
            if kit and kit != "." and kit not in kits:
                kits.append(kit)
        return kits

    def requested_agent_specs(self) -> List[AgentSpec]:
        """Every agent whose kit is attached: the primary plus any agent kit in `kits`."""
        specs: List[AgentSpec] = []
        for kit in [self.agent] + self.all_kits():
            key = os.path.basename(kit.rstrip("/\\")) if ("/" in kit or "\\" in kit) else kit
            spec = AGENT_SPECS.get(key)
            if spec and spec.has_kit and spec not in specs:
                specs.append(spec)
        return specs

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "AgyConfig":
        data = dict(data or {})
        notes: List[str] = []

        # --- pre-0.5 layout -------------------------------------------------
        legacy_sbx = data.pop("sbx", None)
        if isinstance(legacy_sbx, bool):
            legacy_sbx = {}
        if isinstance(legacy_sbx, dict):
            notes.append("the `sbx:` block is gone; agent, kits, clone, ports and host_browser now sit at the top level")
            for key in ("agent", "kits", "clone", "ports", "host_browser", "auth_mode"):
                if key in legacy_sbx and key not in data:
                    data[key] = legacy_sbx[key]
        if data.pop("use_native_login", None) is not None:
            notes.append("`use_native_login` is gone; logins are governed by `auth_mode` (sandbox-local or proxy-managed)")
        if data.pop("workspace_path", None) is not None:
            notes.append("`workspace_path` is gone; sbx mounts the workspace at its host path")
        runtime = data.pop("runtime", None)
        if isinstance(runtime, dict):
            notes.append("`runtime:` is gone; use `build_args` (PYTHON_VERSION, NODE_VERSION, APT_PACKAGES)")
            build_args = data.setdefault("build_args", None) or {}
            if "python" in runtime:
                build_args.setdefault("PYTHON_VERSION", str(runtime["python"]))
            if "node" in runtime:
                build_args.setdefault("NODE_VERSION", str(runtime["node"]))
            if "apt_packages" in runtime:
                build_args.setdefault("APT_PACKAGES", " ".join(runtime["apt_packages"]))
            data["build_args"] = build_args

        raw_auth = data.get("auth_mode") or AUTH_MODE_SANDBOX_LOCAL
        if raw_auth in LEGACY_AUTH_MODES:
            notes.append(f"`auth_mode: {raw_auth}` is now `auth_mode: {LEGACY_AUTH_MODES[raw_auth]}`")
            raw_auth = LEGACY_AUTH_MODES[raw_auth]
        if raw_auth not in AUTH_MODES:
            raise ConfigError(f"auth_mode must be one of {', '.join(AUTH_MODES)} (got '{raw_auth}')")

        agent = data.get("agent") or AGENT_AGY
        if agent not in AGENT_SPECS:
            raise ConfigError(f"Unknown agent '{agent}'.", hint=f"Available agents: {', '.join(AGENT_SPECS)}")

        sandbox_id = data.get("sandbox_id")
        if sandbox_id is not None:
            sandbox_id = str(sandbox_id)
            if not _SANDBOX_ID.fullmatch(sandbox_id):
                raise ConfigError("sandbox_id is not one agy-sandbox wrote (32 hex characters).",
                                  hint="It identifies this project's sandboxes; restore it from git rather than editing it.")
        raw_version = data.get("sandbox_version")
        try:
            sandbox_version = 1 if raw_version is None else int(raw_version)
        except (TypeError, ValueError):
            raise ConfigError(f"sandbox_version must be a whole number (got {data.get('sandbox_version')!r}).")
        if sandbox_version < 1:
            raise ConfigError(f"sandbox_version must be 1 or more (got {sandbox_version}).")

        kit_args = data.get("kit_args") or {}
        return cls(
            profile=data.get("profile") or "default",
            project_name=data.get("project_name") or "default_project",
            sandbox_id=sandbox_id,
            sandbox_version=sandbox_version,
            agent=agent,
            kits=[str(k) for k in (data.get("kits") or []) if k],
            auth_mode=raw_auth,
            clone=bool(data.get("clone", False)),
            ports=[str(p) for p in (data.get("ports") or []) if p],
            host_browser=HostBrowserConfig.from_dict(data.get("host_browser")),
            kit_args={str(k): str(v) for k, v in kit_args.items()},
            base_image=str(data.get("base_image") or DEFAULT_BASE_IMAGE),
            build_args={str(k): str(v) for k, v in (data.get("build_args") or {}).items()},
            setup_scripts=[str(s) for s in (data.get("setup_scripts") or [])],
            env=[str(e) for e in (data.get("env") or [])],
            migration_notes=notes,
        )

    def to_dict(self) -> dict:
        data: dict = {
            "profile": self.profile,
            "project_name": self.project_name,
        }
        if self.sandbox_id is not None:
            data["sandbox_id"] = self.sandbox_id
            data["sandbox_version"] = self.sandbox_version
        data.update({
            "agent": self.agent,
            "kits": list(self.kits),
            "auth_mode": self.auth_mode,
            "clone": self.clone,
        })
        # Optional sections are written only when set, to keep generated files small
        if self.ports:
            data["ports"] = list(self.ports)
        if self.host_browser != HostBrowserConfig():
            data["host_browser"] = self.host_browser.to_dict()
        if self.kit_args:
            data["kit_args"] = dict(self.kit_args)
        if self.base_image != DEFAULT_BASE_IMAGE:
            data["base_image"] = self.base_image
        data["build_args"] = dict(self.build_args)
        data["setup_scripts"] = list(self.setup_scripts)
        data["env"] = list(self.env)
        return data


def load_config(path: str = DEFAULT_CONFIG_FILE) -> AgyConfig:
    if not os.path.exists(path):
        raise ConfigError(
            f"Configuration file '{path}' not found.",
            hint="Run 'agy-sandbox auto-init' to detect project runtimes, or 'agy-sandbox init' to write a template.",
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}")
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level.")
    return AgyConfig.from_dict(data)


def save_config(config: AgyConfig, path: str = DEFAULT_CONFIG_FILE) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config.to_dict(), f, sort_keys=False)


IDENTITY_KEYS = ("sandbox_id", "sandbox_version")


def write_identity(config: AgyConfig, path: str = DEFAULT_CONFIG_FILE) -> None:
    """
    Records the config's sandbox_id and sandbox_version in the file by rewriting only those
    two top-level lines (replacing them where present, else inserting them after project_name),
    so the person's comments and layout survive. The only writer of these fields.
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines(keepends=True)
    wanted = {"sandbox_id": config.sandbox_id, "sandbox_version": str(config.sandbox_version)}
    out: List[str] = []
    seen = set()
    for line in lines:
        key = line.split(":", 1)[0] if ":" in line else ""
        if key in wanted and key not in seen:          # a top-level key starts at column 0
            out.append(f"{key}: {wanted[key]}\n")
            seen.add(key)
        else:
            out.append(line)
    missing = [f"{k}: {wanted[k]}\n" for k in IDENTITY_KEYS if k not in seen]
    if missing and not out:
        out = missing
    elif missing:
        anchor = next((i for i, ln in enumerate(out) if ln.startswith("project_name:")), len(out) - 1)
        if not out[anchor].endswith("\n"):
            out[anchor] += "\n"
        out[anchor + 1:anchor + 1] = missing
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(out))


def migration_warning(config: AgyConfig, path: str = DEFAULT_CONFIG_FILE) -> Optional[str]:
    """A warning block to print when the file used a pre-0.5 layout, or None."""
    if not config.migration_notes:
        return None
    lines = [f"{path} uses a pre-0.5 layout. It still loads, but please update it:"]
    lines += [f"   - {note}" for note in config.migration_notes]
    lines.append("   Run 'agy-sandbox config migrate' to rewrite it in the current layout.")
    return "\n".join(lines)


def default_config(
    agent: str = AGENT_AGY,
    kits: Tuple[str, ...] = (),
    clone: bool = False,
    auth_mode: str = AUTH_MODE_SANDBOX_LOCAL,
    project_name: Optional[str] = None,
) -> AgyConfig:
    """The config `init` writes: the requested agents and kits on the default build."""
    config = AgyConfig(
        project_name=project_name or os.path.basename(os.getcwd()),
        agent=agent,
        auth_mode=auth_mode,
        clone=clone,
        build_args=dict(DEFAULT_BUILD_ARGS),
        setup_scripts=list(DEFAULT_SETUP_SCRIPTS),
        env=list(DEFAULT_ENV_VARS),
    )
    for kit in list(kits):
        if kit and kit not in config.kits:
            config.kits.append(kit)
    config.kits = config.all_kits()
    return config
