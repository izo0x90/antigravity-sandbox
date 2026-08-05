from typing import List, Optional
from .constants import AGENT_SPECS


def build_auto_init_prompt(
    sbx_enabled: bool,
    agent: str,
    clone_enabled: bool,
    kits: Optional[List[str]] = None,
) -> str:
    """
    Constructs an explicit read-only prompt for an AI harness to analyze a project workspace
    and output a raw, structured BoxSpec JSON payload.
    """
    default_kits = []
    if sbx_enabled:
        if agent in AGENT_SPECS:
            kit_ref = AGENT_SPECS[agent].kit_ref
            if kit_ref and kit_ref not in default_kits:
                default_kits.append(kit_ref)
        if "." not in default_kits:
            default_kits.append(".")
        if kits:
            for k in kits:
                if k not in default_kits:
                    default_kits.append(k)

    return (
        "READ-ONLY ANALYSIS INSTRUCTION:\n"
        "Please inspect the current project repository (e.g., pyproject.toml, package.json, Cargo.toml, "
        "mojoproject.toml, pixi.toml, requirements.txt, etc.) in READ-ONLY mode. Do NOT edit, write, or modify any files.\n\n"
        "CRITICAL RULE FOR RUNTIMES (build_args):\n"
        "Include ONLY the runtime keys in `build_args` for languages that are explicitly present in this repository.\n"
        "- If Python files/manifests exist -> include 'PYTHON_VERSION' (e.g. '3.11')\n"
        "- If Node.js files/manifests exist -> include 'NODE_VERSION' (e.g. '20')\n"
        "- If Rust files/manifests exist -> include 'RUST_VERSION' (e.g. 'stable')\n"
        "- If Mojo/Pixi files/manifests exist -> include 'MOJO_VERSION' (e.g. 'latest')\n"
        "OMIT any keys for languages/runtimes that are NOT used in this repository. Do NOT include unused runtimes!\n\n"
        "Generate an optimal sandbox specification payload as a RAW JSON OBJECT. Do NOT include markdown formatting, "
        "preambles, or conversational text.\n\n"
        "EXPECTED JSON SCHEMA:\n"
        "{\n"
        '  "project_name": "<slugified-project-name>",\n'
        '  "profile": "default",\n'
        '  "build_args": {\n'
        '    "<ONLY_PRESENT_RUNTIMES>": "<version_string>"\n'
        "  },\n"
        '  "setup_scripts": ["<inferred setup commands for present languages only, e.g., uv sync>"],\n'
        '  "env": ["ENVIRONMENT=development"],\n'
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
        "CRITICAL: The sandbox environment already has `uv` globally installed. Do NOT add `pip install uv`. "
        "Output ONLY the valid JSON object."
    )
