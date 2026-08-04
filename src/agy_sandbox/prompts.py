from typing import List, Optional
from .constants import AGENT_AGY, DEFAULT_SBX_KIT_URL


def build_auto_init_prompt(
    sbx_enabled: bool,
    agent: str,
    clone_enabled: bool,
    kits: Optional[List[str]] = None,
) -> str:
    """
    Constructs an explicit prompt for the Antigravity agent to analyze a project
    and output an optimal agy.yaml configuration file.
    """
    sbx_instructions = []
    if sbx_enabled:
        sbx_instructions.append("Enable Docker Sandboxes under `sbx` in agy.yaml (`enabled: true`).")
        sbx_instructions.append(f'Set `sbx.agent: "{agent}"`.')
        sbx_instructions.append(f"Set `sbx.clone: {'true' if clone_enabled else 'false'}`.")

        default_kits = []
        if agent == AGENT_AGY:
            default_kits.append(DEFAULT_SBX_KIT_URL)
        default_kits.append(".")
        if kits:
            for k in kits:
                if k not in default_kits:
                    default_kits.append(k)

        sbx_instructions.append(f"Configure `sbx.kits` with these exact entries: {default_kits}.")
    else:
        sbx_instructions.append("Set `sbx.enabled: false`.")

    sbx_prompt_text = " ".join(sbx_instructions)

    return (
        "Please analyze this project repository and automatically generate an optimal `agy.yaml` "
        "file for the agy-sandbox tool. The file should configure the appropriate python version (PYTHON_VERSION), "
        "node version (NODE_VERSION), rust version (RUST_VERSION), and mojo version (MOJO_VERSION) under `build_args` "
        "if relevant codebase files (e.g., Cargo.toml, pyproject.toml, package.json, pixi.toml, mojoproject.toml, *.mojo, etc.) "
        "are present. Also configure appropriate setup_scripts. "
        "Use 'default' for profile and determine the project_name from the folder name. "
        f"SANDBOX CONFIGURATION: {sbx_prompt_text} "
        "IMPORTANT: The sandbox environment already has `uv` globally installed. Do not add `pip install uv` "
        "or similar to setup_scripts. Just use `uv` directly if needed."
    )
