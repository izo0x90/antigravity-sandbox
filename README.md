```text
    ___   ________  __    _____                 ____                
   /   | / ____/\ \/ /   / ___/____ _____  ____/ / /_  ____  _  __
  / /| |/ / __   \  /    \__ \/ __ `/ __ \/ __  / __ \/ __ \| |/_/
 / ___ / /_/ /   / /    ___/ / /_/ / / / / /_/ / /_/ / /_/ />  <  
/_/  |_\____/   /_/    /____/\__,_/_/ /_/\__,_/_.___/\____/_/|_|  
```

# agy-sandbox

The quickest and easiest way to spin up sandboxed development environments using Docker Sandboxes (`sbx`) with all native toolchains and agent dependencies pre-installed.

`agy-sandbox` provisions profile-isolated microVM sandboxes for terminal AI coding agents and interactive development. It bridges custom Docker image building with Docker Sandbox security:

- **Custom Docker Images in Sandboxes**: Build custom project images (`Dockerfile.agy` or base layers) with any native build dependencies, system packages, or toolchains installed, and run them inside isolated microVM sandboxes (`sbx`).
- **Full Development & Agent Runtimes**: Out-of-the-box support for AI coding agents (Google Antigravity, Anthropic Claude Code, OpenCode CLI, OpenAI Codex CLI, Oh My Pi) alongside native compilers (`rustc`, `gcc`), runtimes (`python3`, `node`, `mojo`), and package managers (`uv`, `npm`, `cargo`, `pixi`).
- **Profile-Isolated Workspaces**: Separate logins, tokens, and microVM instances per project and profile, preventing credential bleed across client environments.
- **Active State Protection**: Code Guardian automatically detects active TUI sessions and uncommitted code before container destruction.

## Prerequisites

- **Docker:** Installed and running (Docker Desktop with Docker Sandboxes / `sbx` enabled).
- **uv:** For global Python CLI installation.

## Installation

```bash
uv tool install -e .
```

## Usage & Commands

### ⚡ Quick Start: Smart Auto-Init (`auto-init`)

Stop writing config files by hand. `agy-sandbox auto-init` inspects your codebase in **strict read-only mode** and automatically builds a lean, tailor-made `agy.yaml` specification for your project.

```bash
# Auto-detect project runtimes and generate agy.yaml
agy-sandbox auto-init

# Auto-init with specific container agent harnesses (first agent is primary)
agy-sandbox auto-init --agent opencode,omp,prime-agent

# Use Claude on the host for codebase analysis, but install OpenCode & Prime in container
agy-sandbox auto-init --analyzer claude --agent opencode,prime-agent
```

#### How `auto-init` Works:
1. **Read-Only Codebase Analysis**: Uses `--analyzer <agent>` (or defaults to your first `--agent`) to run a read-only analysis of manifest files (`pyproject.toml`, `package.json`, `Cargo.toml`, `pixi.toml`, `mojoproject.toml`) in plan mode—zero edits or file changes allowed.
2. **Zero-Crash Offline Fallback**: If no AI agent CLI is found on `$PATH`, `agy-sandbox` falls back to its built-in offline manifest scanner to build the spec deterministically.
3. **Lean Runtime Infiltration**: Only language runtimes actually used by your project are included under `build_args`. A pure Python app gets `PYTHON_VERSION`; a Node app gets `NODE_VERSION`. Unused runtimes are completely omitted.
4. **Explicit Flag Precedence**: Any CLI flags you pass (`--agent`, `--analyzer`, `--sbx`, `--clone`, `--dockerfile`, `--with-kit`) strictly override inferred spec fields.
5. **Host-Side Spec Writer**: The AI agent returns a clean JSON spec to stdout, and the host `agy-sandbox` tool writes `agy.yaml` (and optional `Dockerfile.agy`).

### 🛠️ CLI Command Reference

- `agy-sandbox init`  
  Generates a manual template `agy.yaml` in your project workspace.  
  Options:
  - `--agent <agents>`: Container agent harness(es) (comma-separated or repeated). The first agent is primary, and all listed agents are added as kits.
  - `--sbx`: Enable Docker Sandboxes (`sbx`) mode.
  - `--clone`: Enable isolated git clone mode inside the microVM.
  - `--dockerfile`: Scaffold a local, customizable `Dockerfile.agy`.
  - `--with-kit <kit>`: Seed a bundled or local mixin kit (e.g. `chrome-devtools`, `./my-custom-kit`).

- `agy-sandbox auto-init`  
  Smart spec builder. Inspects project manifests in read-only mode and outputs a lean `agy.yaml`.  
  Options:
  - `--analyzer <agent>`: Specify host AI agent for read-only codebase analysis (e.g. `opencode`, `claude`, `agy`). Host-only; not added to container kits unless also in `--agent`.
  - `--agent <agents>`: Container agent harness(es) (e.g. `opencode,omp,prime-agent`). First agent is primary; all listed agents are added to `sbx.kits`.
  - `--sbx`: Configure Docker Sandboxes (`sbx`) mode in auto-generated `agy.yaml`.
  - `--clone`: Configure clone mode in auto-generated `agy.yaml`.
  - `--dockerfile`: Scaffold a customizable `Dockerfile.agy` in project workspace.
  - `--with-kit <kit>`: Add bundled or local mixin kit (can be repeated).

- `agy-sandbox up`  
  Launches or resumes the sandbox microVM and attaches to the agent session.  
  Use `--rebuild` to force rebuild custom images or template stores.

- `agy-sandbox down`  
  Stops and removes the sandbox container for the current project. Scans for unsaved git changes or active TUI logins before destroying.

- `agy-sandbox agents list`  
  Lists all supported agent harnesses and their kit references.

- `agy-sandbox agents add <name>`  
  Configures an agent harness (`claude`, `opencode`, `codex`, `omp`, `shell`) in `agy.yaml`.

- `agy-sandbox kits list`  
  Lists available bundled mixin kits (`chrome-devtools`, `mojo-stdlib`, `omp`).

- `agy-sandbox kits add <name>`  
  Appends a mixin kit to your project's `agy.yaml`.

- `agy-sandbox update-base`  
  Pulls the latest Antigravity engine and CLI from Google and bakes them into `agy-base:latest`.

## Configuration (`agy.yaml`)

```yaml
profile: default
project_name: my_app
agent: claude
auth_mode: sbx_persistent  # 'sbx_persistent' or 'sbx_proxy'
build_args:
  PYTHON_VERSION: "3.11"
  NODE_VERSION: "20"
  RUST_VERSION: "stable"
  MOJO_VERSION: "latest"
setup_scripts:
  - npm install
  - pip install -r requirements.txt
env:
  - ENVIRONMENT=development
use_native_login: false
sbx:
  enabled: true
  agent: claude
  clone: true
  kits:
    - claude
    - opencode
    - chrome-devtools
```

### Supported Agent Harnesses

| Identifier | Display Name | Entrypoint | SBX Secret Services | Session Guard Target |
| :--- | :--- | :--- | :--- | :--- |
| `agy` | Google Antigravity | `gemini` | `google` | `/root/.gemini` |
| `claude` | Anthropic Claude Code | `claude` | `anthropic` | `/home/agent/.claude.json` |
| `opencode` | OpenCode CLI | `opencode` | `openrouter`, `anthropic`, `openai`, `google` | `/home/agent/.config/opencode/auth.json` |
| `codex` | OpenAI Codex CLI | `codex` | `openai` | `/home/agent/.codex/auth.json` |
| `omp` | Oh My Pi Harness | `shell` | *(none)* | `/home/agent/.omp` |
| `prime-agent` | Prime Agent (RLM / Pi) | `prime-agent` | `prime`, `anthropic`, `openai`, `google` | `/home/agent/.prime` |
| `shell` | Interactive Shell | `shell` | *(none)* | *(cloned git changes)* |

## Bundled Mixin Kits

- **`chrome-devtools`**: Spawns headless Chromium with `chrome-devtools-mcp` server for browser automation and CDP debugging.
- **`omp`**: Terminal AI coding agent and tool harness (Oh My Pi). Pre-baked into stacked `agy-base-omp:latest`.
- **`prime-agent`**: Self-improving RLM coding and research agent harness by Prime Intellect. Pre-baked into stacked `agy-base-prime:latest` with Node.js 22.
- **`mojo-stdlib`**: Installs Bazelisk/Bazel launcher and LLVM `lit` test runner for compiling and testing `modularml/mojo`.

## 🛡️ Code Guardian & Session Safeguards

`agy-sandbox` guards your active sessions and uncommitted code before sandbox destruction:
- **Active Auth Sessions**: Scans container microVMs for session files (`.claude.json`, `.config/opencode/auth.json`, `.codex/auth.json`, `.gemini`, `.omp`, `.prime`).
- **Uncommitted Code**: For cloned sandboxes (`sbx.clone: true`), checks `git status`, unpushed commits (`git cherry`), and local-only branches.
- **Interactive Guard Prompt**: Triggers a confirmation prompt (`[y/N]`) before `up --rebuild` or `down` deletes active sessions or uncommitted work.

## 🔐 Profile-Isolated Environments

Logins and state are isolated per profile and project:
- Sandbox microVMs use profile-aware naming: `agy-sandbox-<project>` (default) or `agy-sandbox-<project>--<profile>`.
- Token stores are isolated on the host under `~/.<agent>_<profile>`.
- Set `auth_mode: sbx_proxy` to route API requests through SBX secret proxies (`sbx secret set <sandbox> <service>`).

## 🔄 Changes & Migration (v1 -> v2)

| Feature | Legacy v1 | Current v2 |
| :--- | :--- | :--- |
| **Agent Support** | Google Antigravity only | 7 agent harnesses (`agy`, `claude`, `opencode`, `codex`, `omp`, `prime-agent`, `shell`) |
| **MicroVM Naming** | `agy-sandbox-<project>` (collided across profiles) | `agy-sandbox-<project>--<profile>` (isolated per profile) |
| **Host Profile Paths** | Hardcoded `~/.gemini_<profile>` | Dynamic per-agent (`~/.claude_<profile>`, `~/.opencode_<profile>`, etc.) |
| **Kit Resolution** | Abstract kit strings prioritized | Local bundled kit directories in `kits/` resolved directly to disk paths |
| **Data Protection** | Basic git status check | Active TUI login session files + full Git status checks |

## License

MIT License - see [LICENSE](LICENSE) for details.
