from typing import List, Optional

from .agents import AGENT_SPECS
from .constants import AUTH_MODES, DEFAULT_BASE_IMAGE, SUPPORTED_BUILD_ARGS
from .kits import bundled_kits
from .workload import PACKAGED_DOCKERFILE

# What docker.io/sbx/shell-image ships (from its published kit descriptor): the floor
# every project workload builds on, so the analyzer does not re-install any of it.
BASE_IMAGE_CONTENTS = (
    "Debian 13 with bash, git, curl, wget, gh, jq, make, ripgrep, tmux, vim, nano, openssh-client, "
    "Python 3.14 with pip and uv, sudo, and a Docker engine with compose and buildx"
)


def get_default_dockerfile_content() -> str:
    """The packaged default workload Dockerfile, as the analyzer should assume it."""
    try:
        return PACKAGED_DOCKERFILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "# Default Dockerfile not available on disk"


def build_auto_init_prompt(
    agent: str,
    clone_enabled: bool,
    kits: Optional[List[str]] = None,
    auth_mode: str = AUTH_MODES[0],
) -> str:
    """
    Constructs a fully dynamic read-only prompt for an AI harness to analyze a project workspace.
    Dynamically injects Level 1 Dockerfile.base content, registered agent specs, bundled mixin kits,
    and runtime specs directly from the application registry.
    """
    default_kits = []
    if agent in AGENT_SPECS and AGENT_SPECS[agent].has_kit:
        default_kits.append(agent)
    for k in kits or []:
        if k and k != "." and k not in default_kits:
            default_kits.append(k)

    # 1. Read the default workload Dockerfile
    default_dockerfile_text = get_default_dockerfile_content()

    # 2. Dynamically list bundled mixin kits on disk
    kit_lines = [
        f"- `{k.name}`: {k.display_name} - {k.description}"
        for k in bundled_kits().values()
    ]
    formatted_kits_list = "\n".join(kit_lines) if kit_lines else "- (No bundled mixin kits found)"

    # 3. Dynamically list registered agent harnesses
    agent_lines = []
    for agent_id, spec in AGENT_SPECS.items():
        if agent_id == "shell":
            continue
        sigs = f" (signature files: {', '.join(spec.signature_files)})" if spec.signature_files else ""
        agent_lines.append(f"- `{agent_id}`: {spec.display_name}{sigs}")
    formatted_agents_list = "\n".join(agent_lines) if agent_lines else "- (No agent specs registered)"

    # 4. Dynamically list registered build args keys
    default_args_keys = ", ".join(f"`{k}`" for k in SUPPORTED_BUILD_ARGS)

    return (
        "READ-ONLY REPOSITORY ANALYSIS & ENVIRONMENT ARCHITECTURE INSTRUCTION:\n"
        "You are acting as a Principal Systems & Infrastructure Engineer inspecting this project repository to generate an optimal container sandbox configuration (`agy.yaml`).\n"
        "Your goal is to analyze every layer of this codebase in READ-ONLY mode and produce a complete, self-contained sandbox specification that makes the workspace 100% buildable, testable, and ready for development out of the box. Do NOT edit, write, or modify any files.\n\n"
        "======================================================================\n"
        "LEVEL 1: PRE-INSTALLED BASE CONTAINER ENVIRONMENT\n"
        "======================================================================\n"
        f"The sandbox runs on `{DEFAULT_BASE_IMAGE}`, which ships: {BASE_IMAGE_CONTENTS}.\n"
        "On top of it the default project Dockerfile below is applied (build_args drive its ARGs):\n\n"
        "```dockerfile\n"
        f"{default_dockerfile_text}\n"
        "```\n\n"
        "INSTRUCTION: Do NOT re-install utilities or tools that are already installed in Level 1 (e.g., `uv`, `curl`, `git`, `build-essential`, `pkg-config`, `libssl-dev`, `sudo`, `jq`, `gh`).\n\n"
        "======================================================================\n"
        "LEVEL 2: PER-PROJECT SPECIFICATION (`agy.yaml`)\n"
        "======================================================================\n"
        "Configure the workspace-specific requirements using `agy.yaml`:\n\n"
        "1. `build_args`:\n"
        f"   - RUNTIMES: Extract exact version numbers ONLY for supported build args ({default_args_keys}). Clean constraint symbols (`>=`, `^`, `~`, `=`). Do NOT invent unsupported build arg keys.\n"
        "   - SYSTEM PACKAGES (`APT_PACKAGES`): Identify any ADDITIONAL C/C++ libraries, CLI tools, or unsupported language runtimes (e.g., `golang-go`, `cmake`, `libpq-dev`) needed specifically by this repository beyond Level 1.\n"
        "   - OMIT unused keys.\n\n"
        "2. `setup_scripts`:\n"
        "   - Ordered list of commands to bootstrap dependencies, code generation, and verification (e.g., `uv sync`, `pnpm install`, `cargo fetch`, `make setup`, `cargo check`). Note: `uv` is already in Level 1, do NOT add `pip install uv`.\n\n"
        "3. `env`:\n"
        "   - Default, non-secret environment variables extracted from `.env.example`, `docker-compose.yml`, etc.\n\n"
        "   NOTE: `setup_scripts` run once inside the sandbox VM when it is created, and `env` is applied to every session.\n\n"
        "4. `kits` (COMPOSABLE MIXIN KITS & AGENTS):\n"
        "   AVAILABLE BUNDLED MIXIN KITS:\n"
        f"{formatted_kits_list}\n\n"
        "   AVAILABLE AGENT HARNESSES:\n"
        f"{formatted_agents_list}\n\n"
        "======================================================================\n"
        "OUTPUT FORMAT\n"
        "======================================================================\n"
        "Output your findings as a single RAW JSON OBJECT adhering strictly to this schema:\n\n"
        "{\n"
        '  "project_name": "<slugified-project-name>",\n'
        '  "profile": "default",\n'
        '  "build_args": {\n'
        '    "<PRESENT_RUNTIME_OR_APT_KEY>": "<extracted_value>"\n'
        "  },\n"
        '  "setup_scripts": [\n'
        '    "<inferred setup and bootstrapping commands>"\n'
        "  ],\n"
        '  "env": [\n'
        '    "ENVIRONMENT=development"\n'
        "  ],\n"
        f'  "agent": "{agent}",\n'
        f'  "kits": {default_kits},\n'
        f'  "auth_mode": "{auth_mode}",\n'
        f'  "clone": {"true" if clone_enabled else "false"},\n'
        '  "recommendations": {\n'
        '    "dockerfile_needed": false,\n'
        '    "reason": "<explanation if custom Dockerfile.agy is recommended>"\n'
        "  }\n"
        "}\n\n"
        "Output ONLY valid JSON without markdown code blocks, preambles, or explanations."
    )
