# Auto-Init Read-Only Spec & Harness Adapter Architecture Plan

## Overview
This document outlines the architecture for transforming `agy-sandbox auto-init` into a **harness-agnostic**, **strictly read-only**, **spec-driven** initialization engine.

Instead of directly outputting or modifying configuration files on disk, AI harnesses analyze the repository in a sandboxed/read-only mode and return a structured **Box Spec JSON Payload**. The host `agy-sandbox` CLI captures stdout, validates the spec payload, merges explicit user-provided CLI flags, and safely writes `agy.yaml` (and optionally `Dockerfile.agy`).

---

## Core Requirements & Design Invariants

### 1. Harness Read-Only Modes & Invocations

Each supported agent harness is invoked using explicit non-interactive, read-only flags:

| Agent Harness | Binary Name | Read-Only CLI Command Template | Isolation & Safety Mechanism |
| :--- | :--- | :--- | :--- |
| **Google Antigravity (`agy`)** | `agy` | `agy --add-dir {cwd} --mode plan --print "{prompt}"` | `--mode plan` explicitly disables edit/write tools; `--print` executes non-interactive stdout mode. |
| **Anthropic Claude Code (`claude`)** | `claude` | `claude -p "{prompt}"` | `-p` / `--print` executes non-interactively; tool edit permissions fail gracefully without interactive user approval. |
| **OpenCode CLI (`opencode`)** | `opencode` | `opencode run --agent plan "{prompt}"` | `--agent plan` restricts opencode tools to read-only mode; runs headless without `--auto`. |
| **OpenAI Codex CLI (`codex`)** | `codex` | `codex exec "{prompt}"` | `exec` runs headless non-interactive session to stdout without tool write permission flags. |
| **Oh My Pi (`omp`)** | `omp` | `omp -p "{prompt}" --tools=read,grep,glob` | `--tools=read,grep,glob` explicitly strips `write`, `edit`, `bash`, and `python` tools from the agent runtime. |

### 2. Harness-Agnostic Discovery & Execution
* **Multi-Harness Support:** Works seamlessly across all supported agent harnesses (`agy`, `claude`, `opencode`, `codex`, `omp`).
* **PATH Auto-Discovery:** If no explicit `--agent` flag is passed, scans `$PATH` for installed agent CLI binaries in priority order (`agy` → `claude` → `opencode` → `codex` → `omp`).
* **Offline Local Fallback:** If no AI harness CLI binary is found on `$PATH`, falls back seamlessly to an offline local Python heuristic engine that inspects project manifest files (`pyproject.toml`, `package.json`, `Cargo.toml`, `mojoproject.toml`, `pixi.toml`, etc.).

### 3. Strict Clean JSON Specification Contract (No Markdown Craziness)
AI harnesses are instructed to output a clean JSON payload without markdown wrappers or explanatory text:

```json
{
  "project_name": "my-cool-project",
  "profile": "default",
  "build_args": {
    "PYTHON_VERSION": "3.11",
    "NODE_VERSION": "20",
    "RUST_VERSION": "stable",
    "MOJO_VERSION": "latest"
  },
  "setup_scripts": [
    "uv sync",
    "npm install"
  ],
  "env": [
    "ENVIRONMENT=development"
  ],
  "sbx": {
    "enabled": true,
    "agent": "claude",
    "clone": true,
    "kits": [
      "claude",
      "."
    ]
  },
  "recommendations": {
    "dockerfile_needed": false,
    "reason": "Standard base image satisfies all Python and Node.js runtime requirements."
  }
}
```

#### Robust 3-Tier Parsing Pipeline:
1. **Strict System Prompt Rule:** Demands `"Output ONLY valid raw JSON. Do not include markdown ticks (\`\`\`json) or conversational preambles."`
2. **Sanitized Host Parser (`extract_json_payload`):** Strips markdown fences (` ```json ` / ` ``` `) and locates outer `{` and `}` delimiters in stdout before parsing with `json.loads()`.
3. **Fail-Safe Offline Fallback:** If JSON parsing fails or the subprocess errors out, the host CLI falls back to the deterministic offline Python heuristic engine to ensure zero crashes.

### 4. Explicit Flag Precedence
* **User Flags Win:** Any parameters explicitly provided as CLI flags (`--agent`, `--sbx`, `--with-kit`, `--clone`, `--profile`, etc.) override corresponding values in the AI harness spec.
* **Inference Scope:** AI harnesses are prompted to analyze and infer *only* unprovided codebase fields (such as runtime versions, setup commands, and system dependencies).

---

## Component Architecture

### A. Harness Execution Adapter (`constants.py`)
Extend `AgentSpec` to store CLI binary names and read-only invocation command templates:

```python
@dataclass(frozen=True)
class AgentSpec:
    identifier: str
    display_name: str
    sbx_agent_arg: str
    kit_ref: str
    cli_binary: str                              # e.g., "agy", "claude", "opencode", "codex", "omp"
    read_only_cmd_args: Tuple[str, ...]          # e.g., ("--add-dir", "{cwd}", "--mode", "plan", "--print", "{prompt}")
    sbx_secret_services: Tuple[str, ...] = ()
    signature_files: Tuple[str, ...] = ()
    auth_session_files: Tuple[str, ...] = ()
    profile_dir_name: str = ".gemini"
```

### B. Prompt Construction & Instruction Design (`prompts.py`)
* Construct explicit read-only prompts informing the agent of user-specified CLI flags.
* Require strict JSON codeblock formatting.
* Direct the agent to inspect manifests without attempting to run shell commands or edit files.

### C. JSON Spec Parsing & Offline Fallback Engine (`engine.py`)
* `parse_box_spec_from_output(raw_output: str) -> dict`: Extracts JSON payload from raw agent stdout and validates required schema fields.
* `generate_offline_box_spec(cwd: str, explicit_args: dict) -> dict`: Offline Python scanner that inspects file trees deterministically when no AI harness CLI is installed.

### D. CLI Controller Integration (`cli.py`)
1. Resolve harness binary or offline fallback engine.
2. Build prompt and execute harness read-only process.
3. Parse returned JSON payload into `BoxSpec`.
4. Apply explicit CLI flag overrides.
5. Write final `agy.yaml` and optional `Dockerfile.agy`.

---

## Verification Plan

1. **Read-Only Verification:** Confirm no files are created or modified by agent subprocesses before `agy.yaml` is written by the host CLI.
2. **Harness Adapter Testing:** Test `auto-init` across all 5 harness adapters (`agy`, `claude`, `opencode`, `codex`, `omp`).
3. **Offline Fallback Testing:** Verify offline scanner generates valid specs when `$PATH` contains no agent binaries.
4. **Flag Override Testing:** Ensure user-supplied CLI flags strictly override inferred spec fields.
5. **Unit Test Suite:** Maintain 100% test pass rate with `uv run python -m unittest discover tests`.
