from pathlib import Path
from typing import List, Optional

from .constants import AGENT_SPECS, BASE_DOCKERFILE_NAME, SUPPORTED_BUILD_ARGS, DEFAULT_ENV_VARS
from .kits import list_bundled_kits


def get_base_dockerfile_content() -> str:
    """Reads the raw Dockerfile.base content directly from disk."""
    base_dockerfile_path = Path(__file__).resolve().parent / "docker" / BASE_DOCKERFILE_NAME
    if base_dockerfile_path.exists():
        try:
            with open(base_dockerfile_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return "# Base Dockerfile not available on disk"


def build_auto_init_prompt(
    sbx_enabled: bool,
    agent: str,
    clone_enabled: bool,
    kits: Optional[List[str]] = None,
) -> str:
    """
    Constructs a fully dynamic read-only prompt for an AI harness to analyze a project workspace.
    Dynamically injects Level 1 Dockerfile.base content, registered agent specs, bundled mixin kits,
    and runtime specs directly from the application registry.
    """
    default_kits = []
    if sbx_enabled:
        if agent in AGENT_SPECS:
            kit_ref = AGENT_SPECS[agent].kit_ref
            if kit_ref and kit_ref not in default_kits and kit_ref != ".":
                default_kits.append(kit_ref)
        if kits:
            for k in kits:
                if k and k != "." and k not in default_kits:
                    default_kits.append(k)

    # 1. Read Level 1 Base Dockerfile
    base_dockerfile_text = get_base_dockerfile_content()

    # 2. Dynamically list bundled mixin kits on disk
    bundled_kits = list_bundled_kits()
    kit_lines = [
        f"- `{k['name']}`: {k['display_name']} - {k['description']}"
        for k in bundled_kits
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
        "LEVEL 1: PRE-INSTALLED BASE CONTAINER ENVIRONMENT (`Dockerfile.base`)\n"
        "======================================================================\n"
        "The sandbox container is built on top of `agy-base`, defined by this exact Dockerfile:\n\n"
        "```dockerfile\n"
        f"{base_dockerfile_text}\n"
        "```\n\n"
        "INSTRUCTION: Do NOT re-install utilities or tools that are already installed in Level 1 (e.g., `uv`, `curl`, `git`, `build-essential`, `pkg-config`, `libssl-dev`, `sudo`).\n\n"
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
        "4. `sbx.kits` (COMPOSABLE MIXIN KITS & AGENTS):\n"
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
        '  "sbx": {\n'
        f'    "enabled": {"true" if sbx_enabled else "false"},\n'
        f'    "agent": "{agent}",\n'
        f'    "clone": {"true" if clone_enabled else "false"},\n'
        f'    "kits": {default_kits}\n'
        "  },\n"
        '  "recommendations": {\n'
        '    "dockerfile_needed": false,\n'
        '    "reason": "<explanation if custom Dockerfile.agy is recommended>"\n'
        "  }\n"
        "}\n\n"
        "Output ONLY valid JSON without markdown code blocks, preambles, or explanations."
    )
