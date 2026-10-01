import re
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# File & Directory Names
DEFAULT_CONFIG_FILE = "agy.yaml"
LOCAL_DOCKERFILE_NAME = "Dockerfile.agy"
DEFAULT_DOCKERFILE_NAME = "Dockerfile.default"
BASE_DOCKERFILE_NAME = "Dockerfile.base"
OMP_DOCKERFILE_NAME = "Dockerfile.omp"
PRIME_DOCKERFILE_NAME = "Dockerfile.prime"
CLAUDE_DOCKERFILE_NAME = "Dockerfile.claude"
OPENCODE_DOCKERFILE_NAME = "Dockerfile.opencode"
CODEX_DOCKERFILE_NAME = "Dockerfile.codex"

# Image Tags
DEFAULT_BASE_IMAGE = "agy-base:latest"
OMP_BASE_IMAGE = "agy-base-omp:latest"
PRIME_BASE_IMAGE = "agy-base-prime:latest"
CLAUDE_BASE_IMAGE = "agy-base-claude:latest"
OPENCODE_BASE_IMAGE = "agy-base-opencode:latest"
CODEX_BASE_IMAGE = "agy-base-codex:latest"

# Agent Targets
AGENT_AGY = "agy"
AGENT_CLAUDE = "claude"
AGENT_OPENCODE = "opencode"
AGENT_CODEX = "codex"
AGENT_SHELL = "shell"
AGENT_OMP = "omp"
AGENT_PRIME_AGENT = "prime-agent"

# Kit Names & Remote URLs
KIT_OMP = "omp"
KIT_PRIME_AGENT = "prime-agent"
DEFAULT_SBX_KIT_URL = "git+https://github.com/shelajev/agy-sbx-kit.git"
REMOTE_KIT_SCHEMES = ("git+", "git@", "https://", "http://", "oci://")

# Container Paths & Mount Points
CONTAINER_GEMINI_HOME = "/root/.gemini"
CONTAINER_GEMINI_CONFIG = "/root/.config/gemini"
CONTAINER_KEYRINGS = "/root/.local/share/keyrings"

# Execution Timeouts & Limits
DEFAULT_CMD_TIMEOUT = 10
REMOVE_CMD_TIMEOUT = 15
DAEMON_PREFLIGHT_TIMEOUT = 5
SHORT_IMAGE_ID_LEN = 12
BANNER_WIDTH = 78
EXIT_CODE_INTERRUPTED = 130

# Default Config Specifications
SUPPORTED_BUILD_ARGS = ("PYTHON_VERSION", "NODE_VERSION", "RUST_VERSION", "MOJO_VERSION")
DEFAULT_BUILD_ARGS = {}
DEFAULT_SETUP_SCRIPTS = []
DEFAULT_ENV_VARS = ["ENVIRONMENT=development"]

# Terminal Support Defaults
DEFAULT_TERM = "xterm-256color"
DEFAULT_COLORTERM = "truecolor"
SUPPORTED_TERMINALS = {
    "xterm",
    "xterm-color",
    "xterm-256color",
    "screen",
    "screen-256color",
    "tmux",
    "tmux-256color",
    "xterm-kitty",
    "alacritty",
    "ghostty",
    "wezterm",
}


@dataclass(frozen=True)
class AgentSpec:
    identifier: str
    display_name: str
    sbx_agent_arg: str
    kit_ref: str
    cli_binary: str = ""
    dockerfile_name: str = ""
    base_image_tag: str = ""
    read_only_args: Tuple[str, ...] = ("-p", "{prompt}")
    sbx_secret_services: Tuple[str, ...] = ()
    signature_files: Tuple[str, ...] = ()
    auth_session_files: Tuple[str, ...] = ()
    profile_dir_name: str = ".gemini"

    def get_read_only_cmd(self, prompt: str, cwd: str) -> List[str]:
        if not self.cli_binary or not self.read_only_args:
            return []
        return [self.cli_binary] + [arg.format(prompt=prompt, cwd=cwd) for arg in self.read_only_args]


# =============================================================================
# MULTI-AGENT SANDBOXES: HOW CLAUDE / OPENCODE / CODEX ARE WIRED
# sbx's native agents (claude, opencode, codex) are full sandbox kits, and sbx
# allows only one per sandbox (https://github.com/docker/sbx-releases/issues/594).
# To install several harnesses side by side we do NOT use sbx's native agents:
#   - kits/<agent>/spec.yaml is the official sbx kit spec vendored from
#     docker/sbx-kits-contrib and converted to a mixin (config, creds, network)
#   - docker/Dockerfile.<agent> installs the binary as a stacked base layer
#   - sbx_agent_arg="shell", so the sandbox launches a shell and every harness
#     is run from it
# TODO(REFACTOR): agent/kit handling (AgentSpec, kits.py, cli.py) grew by
# patching. Any agent touching it next should consolidate it around this model.
# =============================================================================
AGENT_SPECS: Dict[str, AgentSpec] = {
    "agy": AgentSpec(
        identifier="agy",
        display_name="Google Antigravity",
        sbx_agent_arg="agy",
        kit_ref=DEFAULT_SBX_KIT_URL,
        cli_binary="agy",
        read_only_args=("--add-dir", "{cwd}", "--mode", "plan", "--print", "{prompt}"),
        sbx_secret_services=("google",),
        signature_files=("antigravity.yaml",),
        auth_session_files=(".gemini",),
        profile_dir_name=".gemini",
    ),
    "claude": AgentSpec(
        identifier="claude",
        display_name="Anthropic Claude Code",
        sbx_agent_arg="shell",
        kit_ref="claude",
        cli_binary="claude",
        dockerfile_name=CLAUDE_DOCKERFILE_NAME,
        base_image_tag=CLAUDE_BASE_IMAGE,
        read_only_args=("-p", "{prompt}"),
        sbx_secret_services=("anthropic",),
        signature_files=("CLAUDE.md", ".claude"),
        auth_session_files=(".claude.json", ".claude"),
        profile_dir_name=".claude",
    ),
    "opencode": AgentSpec(
        identifier="opencode",
        display_name="OpenCode CLI",
        sbx_agent_arg="shell",
        kit_ref="opencode",
        cli_binary="opencode",
        dockerfile_name=OPENCODE_DOCKERFILE_NAME,
        base_image_tag=OPENCODE_BASE_IMAGE,
        read_only_args=("run", "--agent", "plan", "{prompt}"),
        sbx_secret_services=("openrouter", "anthropic", "openai", "google"),
        signature_files=("opencode.json", ".opencode"),
        auth_session_files=(".config/opencode/auth.json", ".opencode"),
        profile_dir_name=".opencode",
    ),
    "codex": AgentSpec(
        identifier="codex",
        display_name="OpenAI Codex CLI",
        sbx_agent_arg="shell",
        kit_ref="codex",
        cli_binary="codex",
        dockerfile_name=CODEX_DOCKERFILE_NAME,
        base_image_tag=CODEX_BASE_IMAGE,
        read_only_args=("exec", "{prompt}"),
        sbx_secret_services=("openai",),
        signature_files=(".codex",),
        auth_session_files=(".codex/auth.json", ".codex"),
        profile_dir_name=".codex",
    ),
    "omp": AgentSpec(
        identifier="omp",
        display_name="Oh My Pi Harness",
        sbx_agent_arg="shell",
        kit_ref="omp",
        cli_binary="omp",
        dockerfile_name=OMP_DOCKERFILE_NAME,
        base_image_tag=OMP_BASE_IMAGE,
        read_only_args=("-p", "{prompt}", "--tools=read,grep,glob"),
        signature_files=(),
        auth_session_files=(".omp",),
        profile_dir_name=".omp",
    ),
    "prime-agent": AgentSpec(
        identifier="prime-agent",
        display_name="Prime Agent (RLM)",
        sbx_agent_arg="shell",
        kit_ref="prime-agent",
        cli_binary="prime-agent",
        dockerfile_name=PRIME_DOCKERFILE_NAME,
        base_image_tag=PRIME_BASE_IMAGE,
        read_only_args=("-p", "--no-session", "--no-tools", "{prompt}"),
        sbx_secret_services=("prime", "anthropic", "openai", "google"),
        signature_files=("AGENTS.md", ".prime"),
        auth_session_files=(".prime",),
        profile_dir_name=".prime",
    ),
    "shell": AgentSpec(
        identifier="shell",
        display_name="Interactive Shell",
        sbx_agent_arg="shell",
        kit_ref="",
        cli_binary="",
        read_only_args=(),
        signature_files=(),
        auth_session_files=(),
        profile_dir_name=".shell",
    ),
}


def discover_available_agent(preferred_agent: Optional[str] = None) -> Optional[AgentSpec]:
    """
    Scans PATH for available agent binaries. If preferred_agent is provided and installed, returns it.
    Otherwise checks agents in priority order ('agy', 'claude', 'opencode', 'codex', 'omp').
    Returns None if no agent binary is found on PATH.
    """
    if preferred_agent and preferred_agent in AGENT_SPECS:
        spec = AGENT_SPECS[preferred_agent]
        if spec.cli_binary and shutil.which(spec.cli_binary):
            return spec

    priority = ("agy", "claude", "opencode", "codex", "omp", "prime-agent")
    for agent_id in priority:
        spec = AGENT_SPECS[agent_id]
        if spec.cli_binary and shutil.which(spec.cli_binary):
            return spec

    return None


def slugify_project_name(name: str) -> str:
    """
    Converts arbitrary project names into valid, clean Docker/DNS-compatible slugs.
    Handles spaces, underscores, uppercase letters, and special characters.
    """
    if not name:
        return "default-project"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug if slug else "default-project"
