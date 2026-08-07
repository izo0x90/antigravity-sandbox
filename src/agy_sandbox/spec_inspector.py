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
    env_vars = list(DEFAULT_ENV_VARS)

    files = set(os.listdir(cwd)) if os.path.isdir(cwd) else set()

    print("🔍 [Offline Inspector] Programmatically scanning repository manifests...")

    # Python
    if any(f in files for f in ("pyproject.toml", "requirements.txt", "Pipfile", "environment.yml", ".python-version")):
        python_ver = DEFAULT_BUILD_ARGS.get("PYTHON_VERSION", "3.11")
        py_file = "pyproject.toml" if "pyproject.toml" in files else ("requirements.txt" if "requirements.txt" in files else ".python-version")
        if ".python-version" in files:
            try:
                with open(os.path.join(cwd, ".python-version"), "r", encoding="utf-8") as f:
                    ver_line = f.read().strip()
                    match = re.search(r"([0-9]+\.[0-9]+)", ver_line)
                    if match:
                        python_ver = match.group(1)
            except Exception:
                pass
        elif "pyproject.toml" in files:
            try:
                with open(os.path.join(cwd, "pyproject.toml"), "r", encoding="utf-8") as f:
                    content = f.read()
                    match = re.search(r'(?:requires-python|python)\s*=\s*"[\^~>=]*([0-9]+\.[0-9]+)"', content)
                    if match:
                        python_ver = match.group(1)
            except Exception:
                pass

        build_args["PYTHON_VERSION"] = python_ver

        py_setup = "uv sync" if ("uv.lock" in files or "pyproject.toml" in files) else "pip install -r requirements.txt"
        setup_scripts.append(py_setup)
        print(f"  * Python: Found {py_file} -> extracted PYTHON_VERSION={python_ver} (setup: {py_setup})")

    # Node.js
    if any(f in files for f in ("package.json", ".nvmrc", ".node-version")):
        node_ver = DEFAULT_BUILD_ARGS.get("NODE_VERSION", "20")
        node_file = "package.json" if "package.json" in files else (".nvmrc" if ".nvmrc" in files else ".node-version")
        if ".nvmrc" in files or ".node-version" in files:
            nvm_file = ".nvmrc" if ".nvmrc" in files else ".node-version"
            try:
                with open(os.path.join(cwd, nvm_file), "r", encoding="utf-8") as f:
                    ver_line = f.read().strip()
                    match = re.search(r"([0-9]+)", ver_line)
                    if match:
                        node_ver = match.group(1)
            except Exception:
                pass
        elif "package.json" in files:
            try:
                with open(os.path.join(cwd, "package.json"), "r", encoding="utf-8") as f:
                    content = f.read()
                    match = re.search(r'"node":\s*"[\^~>=]*([0-9]+)"', content)
                    if match:
                        node_ver = match.group(1)
            except Exception:
                pass

        build_args["NODE_VERSION"] = node_ver

        node_setup = "pnpm install" if "pnpm-lock.yaml" in files else ("yarn install" if "yarn.lock" in files else "npm install")
        setup_scripts.append(node_setup)
        print(f"  * Node.js: Found {node_file} -> extracted NODE_VERSION={node_ver} (setup: {node_setup})")

    # Rust
    if any(f in files for f in ("Cargo.toml", "rust-toolchain.toml", "rust-toolchain")):
        rust_ver = DEFAULT_BUILD_ARGS.get("RUST_VERSION", "stable")
        
        toolchain_file = "rust-toolchain.toml" if "rust-toolchain.toml" in files else ("rust-toolchain" if "rust-toolchain" in files else "Cargo.toml")
        if toolchain_file in ("rust-toolchain.toml", "rust-toolchain"):
            try:
                with open(os.path.join(cwd, toolchain_file), "r", encoding="utf-8") as f:
                    content = f.read()
                    match = re.search(r'channel\s*=\s*"([^"]+)"', content)
                    if match:
                        rust_ver = match.group(1)
            except Exception:
                pass
        elif "Cargo.toml" in files:
            try:
                with open(os.path.join(cwd, "Cargo.toml"), "r", encoding="utf-8") as f:
                    content = f.read()
                    match = re.search(r'rust-version\s*=\s*"([^"]+)"', content)
                    if match:
                        rust_ver = match.group(1)
            except Exception:
                pass

        build_args["RUST_VERSION"] = rust_ver
        setup_scripts.append("cargo fetch")
        print(f"  * Rust: Found {toolchain_file} -> extracted RUST_VERSION={rust_ver} (setup: cargo fetch)")

    # Go
    if "go.mod" in files:
        go_ver = "1.22"
        try:
            with open(os.path.join(cwd, "go.mod"), "r", encoding="utf-8") as f:
                content = f.read()
                match = re.search(r"^go\s+([0-9]+\.[0-9]+)", content, flags=re.MULTILINE)
                if match:
                    go_ver = match.group(1)
        except Exception:
            pass
        build_args["GO_VERSION"] = go_ver
        setup_scripts.append("go mod download")
        print(f"  * Go: Found go.mod -> extracted GO_VERSION={go_ver} (setup: go mod download)")

    # Mojo / Pixi
    if any(f in files for f in ("mojoproject.toml", "pixi.toml")) or any(f.endswith(".mojo") for f in files):
        mojo_ver = DEFAULT_BUILD_ARGS.get("MOJO_VERSION", "latest")
        build_args["MOJO_VERSION"] = mojo_ver
        mojo_file = "mojoproject.toml" if "mojoproject.toml" in files else ("pixi.toml" if "pixi.toml" in files else "mojo source")
        print(f"  * Mojo: Found {mojo_file} -> extracted MOJO_VERSION={mojo_ver}")

    # Makefile setup target detection
    if "Makefile" in files:
        try:
            with open(os.path.join(cwd, "Makefile"), "r", encoding="utf-8") as f:
                content = f.read()
                if re.search(r"^setup:", content, flags=re.MULTILINE):
                    if "make setup" not in setup_scripts:
                        setup_scripts.append("make setup")
                        print("  * Build: Found Makefile -> added 'make setup' to setup_scripts")
                elif re.search(r"^init:", content, flags=re.MULTILINE):
                    if "make init" not in setup_scripts:
                        setup_scripts.append("make init")
                        print("  * Build: Found Makefile -> added 'make init' to setup_scripts")
        except Exception:
            pass

    # Environmental variable inference from .env.example
    if ".env.example" in files or ".env.template" in files:
        env_file = ".env.example" if ".env.example" in files else ".env.template"
        try:
            added_count = 0
            with open(os.path.join(cwd, env_file), "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k and not any(secret in k.lower() for secret in ("key", "secret", "token", "password")):
                            env_entry = f"{k}={v}"
                            if env_entry not in env_vars:
                                env_vars.append(env_entry)
                                added_count += 1
            if added_count > 0:
                print(f"  * Environment: Found {env_file} -> extracted {added_count} default variable(s)")
        except Exception:
            pass

    agent = explicit.get("agent", "agy")
    sbx_enabled = explicit.get("sbx", False)
    clone_enabled = explicit.get("clone", False)
    kits = list(explicit.get("kits", []))

    # Smart kit auto-detection during offline scan
    # Check for browser automation dependencies -> add chrome-devtools
    for manifest_file in ("package.json", "pyproject.toml", "Cargo.toml", "requirements.txt"):
        if manifest_file in files:
            try:
                with open(os.path.join(cwd, manifest_file), "r", encoding="utf-8") as f:
                    manifest_content = f.read().lower()
                    if any(browser_tool in manifest_content for browser_tool in ("playwright", "puppeteer", "selenium", "cypress")):
                        if "chrome-devtools" not in kits:
                            kits.append("chrome-devtools")
                        break
            except Exception:
                pass

    # Check for Mojo / Pixi stdlib development -> add mojo-stdlib
    if any(f in files for f in ("mojoproject.toml", "pixi.toml")) or any(f.endswith(".mojo") for f in files):
        if "mojo-stdlib" not in kits:
            kits.append("mojo-stdlib")

    return {
        "project_name": project_name,
        "profile": "default",
        "build_args": build_args,
        "setup_scripts": setup_scripts,
        "env": env_vars,
        "sbx": {
            "enabled": sbx_enabled,
            "agent": agent,
            "clone": clone_enabled,
            "kits": kits,
        },
        "recommendations": {
            "dockerfile_needed": False,
            "reason": "Offline local inspection generated spec from project manifests.",
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
