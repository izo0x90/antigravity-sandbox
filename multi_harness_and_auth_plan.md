# Implementation Plan: Multi-Harness & Tri-Mode Auth System for `agy-sandbox`

## Executive Summary
This plan details the addition of first-class support for **Claude Code (`claude`)**, **OpenCode (`opencode`)**, and **OpenAI Codex (`codex`)** to `agy-sandbox`, complementing Google Antigravity (`agy`). It introduces a tri-mode authentication engine (`auth_mode`: `profile_mount` | `sbx_proxy` | `native_passthrough`) that handles interactive TUI logins, profile-isolated persistent sessions, and Docker Sandboxes (`sbx`) in-flight secret proxying, incorporating real-world CLI package realities and Docker volume mount safety rules.

---

## 1. Verified Agent Specifications (`AGENT_SPECS`)

We establish a data specification registry in `src/agy_sandbox/constants.py` mapping container paths, npm/install targets, and environment/SBX proxy keys:

| Agent (`agent`) | Target Harness | NPM / Install Target | Container Relative Home Path | Container Config Files | Host Default Path | SBX Secret Key |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `agy` | Google Antigravity | `https://antigravity.google/cli/install.sh` | `.gemini` | `.config/gemini`, `.local/share/keyrings` | `~/.gemini` | `google` |
| `claude` | Anthropic Claude Code | `@anthropic-ai/claude-code` | `.claude` | `.claude.json` (symlinked into `.claude/`) | `~/.claude` | `anthropic` |
| `opencode` | OpenCode CLI | `opencode-ai` | `.config/opencode` | `.local/share/opencode` | `~/.config/opencode` | `openrouter` |
| `codex` | OpenAI Codex CLI | `openai` | `.codex` | N/A | `~/.codex` | `openai` |
| `shell` | Interactive Terminal | N/A | N/A | N/A | N/A | N/A |
| `omp` | OMP Harness | N/A | `.omp` | N/A | `~/.omp` | N/A |

---

## 2. Tri-Mode Authentication Architecture (`auth_mode`)

To handle interactive TUI logins, host session passthrough, and zero-disk secret proxying, `agy.yaml` supports three explicit authentication modes:

```yaml
profile: default
project_name: my_app
agent: claude            # Choices: agy, claude, opencode, codex, shell, omp
auth_mode: profile_mount # Choices: profile_mount (default), sbx_proxy, native_passthrough

build_args:
  PYTHON_VERSION: "3.11"
  NODE_VERSION: "20"
  RUST_VERSION: "stable"
  MOJO_VERSION: "latest"

sbx:
  enabled: true
  agent: claude
  clone: true
  kits: []
```

### Mode 1: `profile_mount` (Default - Best for Interactive TUI Logins)
- **Mechanism (Docker Engine)**: Volume-mounts a profile-isolated directory on the host (`~/.<agent>_<profile>`) to the container's relative home directory (`$HOME/.claude`, `$HOME/.config/opencode`, `$HOME/.codex`).
- **Safety Rule**: Directory-to-directory mounts only! Single-file bind mounts (like `.claude.json`) are avoided to prevent Docker from creating directories on the host if files don't exist and to allow atomic file renames in Node.js `fs`. Symlinks inside the container map config files (`$HOME/.claude.json -> $HOME/.claude/claude.json`).
- **Use Case**: Preserves interactive TUI login sessions, OAuth tokens, and user preferences per project without re-authenticating on restart.

### Mode 2: `sbx_proxy` (Best for Headless / Secret Proxying)
- **Mechanism (Docker Sandboxes `sbx`)**: Bypasses host filesystem directory mounts. Leverages Docker Sandboxes native `sbx secret set <sandbox> <service>` host proxy.
- **Use Case**: Injects in-flight API key substitution without writing plain-text keys or OAuth tokens to container or host disks.

### Mode 3: `native_passthrough`
- **Mechanism**: Mounts the host machine's unisolated default directory (`~/.claude`, `~/.config/opencode`, `~/.codex`) directly into the container.
- **Use Case**: Direct passthrough of existing host sessions.

---

## 3. Step-by-Step Technical Implementation Tasks

### Task 1: `src/agy_sandbox/constants.py`
- Add `@dataclass class AgentSpec` and `AGENT_SPECS` dictionary mapping `agy`, `claude`, `opencode`, `codex`, `shell`, and `omp`.
- Define `AUTH_MODE_PROFILE_MOUNT = "profile_mount"`, `AUTH_MODE_SBX_PROXY = "sbx_proxy"`, and `AUTH_MODE_NATIVE_PASSTHROUGH = "native_passthrough"`.

### Task 2: `src/agy_sandbox/config.py`
- Update `AgyConfig` dataclass to include `agent: str = AGENT_AGY` and `auth_mode: str = AUTH_MODE_PROFILE_MOUNT`.
- Ensure `sbx.agent` inherits from `config.agent` if not explicitly set.
- Update `write_default_config()` and `AgyConfig.from_dict()` to parse `agent` and `auth_mode`.

### Task 3: `src/agy_sandbox/docker/Dockerfile.default`
- Install `@anthropic-ai/claude-code` and `opencode-ai` globally via NPM so all agent binaries are pre-baked on `$PATH`.

### Task 4: `src/agy_sandbox/runners/docker.py`
- Refactor volume mounting logic to use `$HOME` relative paths from `AGENT_SPECS[config.agent]`.
- Ensure directory-only volume mounts (`profile_dir` $\rightarrow$ `$HOME/.claude`).
- Conditionally run `dbus-run-session` and `gnome-keyring-daemon` startup script **only** when `config.agent == "agy"`.

### Task 5: `src/agy_sandbox/runners/sbx.py`
- Implement `sbx secret` integration when `auth_mode: sbx_proxy`.
- Ensure proper volume or kit attachment based on `config.agent` and `config.auth_mode`.

### Task 6: `src/agy_sandbox/prompts.py` & `cli.py`
- Update `auto-init` to scan project files:
  - `CLAUDE.md` / `.claude/` $\rightarrow$ Recommend `agent: claude`
  - `opencode.json` / `.opencode/` $\rightarrow$ Recommend `agent: opencode`
  - `.codex/` $\rightarrow$ Recommend `agent: codex`
  - Otherwise default to `agent: agy` or `agent: shell`.

### Task 7: Unit Testing & Verification
- Unit tests in `tests/test_multi_agent_auth.py` for spec resolution, profile folder paths, symlink creation, and `AgyConfig` parsing.

---

## 4. Verification Loop
- Execute `python -m unittest discover tests`.
- Execute `uv run ruff check .`.
- Test image builds and verify CLI availability (`claude --version`, `opencode --version`).
