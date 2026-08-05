import json
import os
import re
import subprocess
from typing import Any, Dict, Optional

from .constants import (
    DEFAULT_BUILD_ARGS,
    DEFAULT_ENV_VARS,
    AgentSpec,
    slugify_project_name,
)


def extract_json_payload(raw_output: str) -> Optional[Dict[str, Any]]:
    """
    Extracts and parses a JSON object from raw agent output.
    Handles raw JSON, markdown code block wrappers (```json ... ```), and trailing text.
    Returns None if no valid JSON object is found.
    """
    if not raw_output or not raw_output.strip():
        return None

    cleaned = raw_output.strip()

    # Remove markdown code fence lines if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    # Try direct parsing first
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Find boundaries of first '{' and last '}'
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")

    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = cleaned[first_brace : last_brace + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return None


def generate_offline_box_spec(cwd: str, explicit_args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Offline local scanner that inspects manifest files in cwd and returns a valid
    BoxSpec dictionary containing ONLY runtimes explicitly present in the repository.
    """
    explicit = explicit_args or {}
    project_name = slugify_project_name(os.path.basename(os.path.abspath(cwd)))

    build_args = {}
    setup_scripts = []

    # Local manifest inspection
    files = set(os.listdir(cwd)) if os.path.isdir(cwd) else set()

    # Python
    if any(f in files for f in ("pyproject.toml", "requirements.txt", "Pipfile", "environment.yml")):
        python_ver = DEFAULT_BUILD_ARGS.get("PYTHON_VERSION", "3.11")
        if "pyproject.toml" in files:
            try:
                with open(os.path.join(cwd, "pyproject.toml"), "r", encoding="utf-8") as f:
                    content = f.read()
                    match = re.search(r'python\s*=\s*"[\^~>=]*([0-9]+\.[0-9]+)"', content)
                    if match:
                        python_ver = match.group(1)
            except Exception:
                pass

        build_args["PYTHON_VERSION"] = python_ver

        if "requirements.txt" in files:
            setup_scripts.append("pip install -r requirements.txt")
        elif "pyproject.toml" in files:
            setup_scripts.append("uv sync")

    # Node.js
    if "package.json" in files:
        node_ver = DEFAULT_BUILD_ARGS.get("NODE_VERSION", "20")
        setup_scripts.append("npm install")
        try:
            with open(os.path.join(cwd, "package.json"), "r", encoding="utf-8") as f:
                content = f.read()
                match = re.search(r'"node":\s*"[\^~>=]*([0-9]+)"', content)
                if match:
                    node_ver = match.group(1)
        except Exception:
            pass
        build_args["NODE_VERSION"] = node_ver

    # Rust
    if "Cargo.toml" in files:
        build_args["RUST_VERSION"] = DEFAULT_BUILD_ARGS.get("RUST_VERSION", "stable")

    # Mojo
    if any(f in files for f in ("mojoproject.toml", "pixi.toml")) or any(f.endswith(".mojo") for f in files):
        build_args["MOJO_VERSION"] = DEFAULT_BUILD_ARGS.get("MOJO_VERSION", "latest")

    agent = explicit.get("agent", "agy")
    sbx_enabled = explicit.get("sbx", False)
    clone_enabled = explicit.get("clone", False)
    kits = list(explicit.get("kits", []))

    return {
        "project_name": project_name,
        "profile": "default",
        "build_args": build_args,
        "setup_scripts": setup_scripts,
        "env": list(DEFAULT_ENV_VARS),
        "sbx": {
            "enabled": sbx_enabled,
            "agent": agent,
            "clone": clone_enabled,
            "kits": kits,
        },
        "recommendations": {
            "dockerfile_needed": False,
            "reason": "Offline local inspection generated default configuration from project manifests.",
        },
    }


def run_read_only_agent_analysis(
    spec: AgentSpec,
    prompt: str,
    cwd: str,
    timeout: int = 60,
) -> Optional[Dict[str, Any]]:
    """
    Executes an agent harness in strict read-only / plan mode, captures stdout,
    and returns the parsed BoxSpec dictionary.
    """
    full_cmd = spec.get_read_only_cmd(prompt, cwd)
    if not full_cmd:
        return None

    try:
        res = subprocess.run(
            full_cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        if res.returncode == 0 and res.stdout:
            return extract_json_payload(res.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass

    return None
