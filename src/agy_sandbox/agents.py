"""
The agent registry. Every agent runs the same way: sbx starts a plain shell on
the project image, and the agent's bundled kit (kits/<identifier>/spec.yaml)
plus its stacked Dockerfile layer (docker/<dockerfile>) provide the binary,
config, credentials and network policy. Nothing outside this file and those two
directories knows an agent by name.
"""
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

AGENT_AGY = "agy"
AGENT_CLAUDE = "claude"
AGENT_OPENCODE = "opencode"
AGENT_CODEX = "codex"
AGENT_OMP = "omp"
AGENT_PRIME_AGENT = "prime-agent"
AGENT_SHELL = "shell"

# Order used when picking a host-side analyzer for auto-init
DISCOVERY_PRIORITY = (AGENT_AGY, AGENT_CLAUDE, AGENT_OPENCODE, AGENT_CODEX, AGENT_OMP, AGENT_PRIME_AGENT)


@dataclass(frozen=True)
class AgentSpec:
    identifier: str
    display_name: str
    cli_binary: str = ""
    # Stacked image layer that installs the binary; empty for agents with no layer (shell)
    dockerfile: str = ""
    image_tag: str = ""
    # Arguments that run the agent in read-only/plan mode for auto-init analysis
    read_only_args: Tuple[str, ...] = ()
    # `sbx secret` services this agent can use in proxy-managed mode
    secret_services: Tuple[str, ...] = ()
    # Files that hint a repo was set up for this agent
    signature_files: Tuple[str, ...] = ()
    # Login/session files (relative to the agent's home) the guardian protects
    session_files: Tuple[str, ...] = ()
    # Whether kits/<identifier> exists; shell is the one agent without a kit
    has_kit: bool = True

    def read_only_cmd(self, prompt: str, cwd: str) -> List[str]:
        if not self.cli_binary or not self.read_only_args:
            return []
        return [self.cli_binary] + [arg.format(prompt=prompt, cwd=cwd) for arg in self.read_only_args]


AGENT_SPECS: Dict[str, AgentSpec] = {
    AGENT_AGY: AgentSpec(
        identifier=AGENT_AGY,
        display_name="Google Antigravity",
        cli_binary="agy",
        dockerfile="Dockerfile.agy",
        image_tag="agy-base-agy:latest",
        read_only_args=("--add-dir", "{cwd}", "--mode", "plan", "--print", "{prompt}"),
        secret_services=("google",),
        signature_files=("antigravity.yaml",),
        session_files=(".gemini",),
    ),
    AGENT_CLAUDE: AgentSpec(
        identifier=AGENT_CLAUDE,
        display_name="Anthropic Claude Code",
        cli_binary="claude",
        dockerfile="Dockerfile.claude",
        image_tag="agy-base-claude:latest",
        read_only_args=("-p", "{prompt}"),
        secret_services=("anthropic",),
        signature_files=("CLAUDE.md", ".claude"),
        session_files=(".claude.json", ".claude/.credentials.json"),
    ),
    AGENT_OPENCODE: AgentSpec(
        identifier=AGENT_OPENCODE,
        display_name="OpenCode CLI",
        cli_binary="opencode",
        dockerfile="Dockerfile.opencode",
        image_tag="agy-base-opencode:latest",
        read_only_args=("run", "--agent", "plan", "{prompt}"),
        secret_services=("openrouter", "anthropic", "openai", "google"),
        signature_files=("opencode.json", ".opencode"),
        session_files=(".local/share/opencode/auth.json", ".config/opencode/auth.json"),
    ),
    AGENT_CODEX: AgentSpec(
        identifier=AGENT_CODEX,
        display_name="OpenAI Codex CLI",
        cli_binary="codex",
        dockerfile="Dockerfile.codex",
        image_tag="agy-base-codex:latest",
        read_only_args=("exec", "{prompt}"),
        secret_services=("openai",),
        signature_files=(".codex",),
        session_files=(".codex/auth.json",),
    ),
    AGENT_OMP: AgentSpec(
        identifier=AGENT_OMP,
        display_name="Oh My Pi Harness",
        cli_binary="omp",
        dockerfile="Dockerfile.omp",
        image_tag="agy-base-omp:latest",
        read_only_args=("-p", "{prompt}", "--tools=read,grep,glob"),
        session_files=(".omp",),
    ),
    AGENT_PRIME_AGENT: AgentSpec(
        identifier=AGENT_PRIME_AGENT,
        display_name="Prime Agent (RLM)",
        cli_binary="prime-agent",
        dockerfile="Dockerfile.prime",
        image_tag="agy-base-prime:latest",
        read_only_args=("-p", "--no-session", "--no-tools", "{prompt}"),
        secret_services=("prime", "anthropic", "openai", "google"),
        signature_files=("AGENTS.md", ".prime"),
        session_files=(".prime",),
    ),
    AGENT_SHELL: AgentSpec(
        identifier=AGENT_SHELL,
        display_name="Interactive Shell",
        has_kit=False,
    ),
}


def discover_available_agent(preferred: Optional[str] = None) -> Optional[AgentSpec]:
    """Returns the preferred agent if its binary is on PATH, else the first installed one by priority."""
    if preferred in AGENT_SPECS:
        spec = AGENT_SPECS[preferred]
        if spec.cli_binary and shutil.which(spec.cli_binary):
            return spec
    for agent_id in DISCOVERY_PRIORITY:
        spec = AGENT_SPECS[agent_id]
        if spec.cli_binary and shutil.which(spec.cli_binary):
            return spec
    return None
