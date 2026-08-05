# SBX Multi-Harness & Explicit Kit Architecture Plan

## Executive Summary
This implementation plan establishes a clean, explicit, and symmetric architecture for supporting multi-agent AI coding harnesses (**`claude`**, **`opencode`**, **`codex`**, **`gemini`**, **`shell`**, **`omp`**) exclusively through **Docker Sandboxes (`sbx`)**:

1. **Explicit `init` $\rightarrow$ Dumb `up` Symmetry**: `init` parses CLI flags (`--agent`, `--with-agent`, `--with-kit`) and resolves all requested agent and tool kits explicitly into `agy.yaml` under `sbx.kits`. `up` reads `sbx.kits` directly as the ground truth without hidden injections or magic runtime guessing.
2. **Unified Kit Resolution**: Every agent and tool capability is represented as a kit. `AGENT_SPECS` in `constants.py` stores the canonical kit source for each agent (whether a local bundled kit directory or a remote git/OCI URL). No unnecessary dummy directories or files are generated.
3. **Per-Project Login Isolation**: `sbx` maintains isolated microVM filesystems per sandbox name (`agy-sandbox-<project>--<profile>`). TUI OAuth logins (Claude Code, OpenCode, Codex) and `sbx secret` proxy keys stay strictly isolated per project.
4. **Active Auth Safeguard & Override**: `git_guardian.py` detects active TUI session auth files before destructive `--rebuild` or `down` operations, prompting the user interactively (`[y/N]`) so they can confirm or override the teardown.

---

## 1. Data-Driven Agent & Kit Registry (`AGENT_SPECS`)

In `src/agy_sandbox/constants.py`, `AgentSpec` maps agent identifiers to their canonical kit reference (local bundled kit or remote URL), native `sbx` positional argument, and workspace signature files:

```python
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

@dataclass(frozen=True)
class AgentSpec:
    identifier: str
    display_name: str
    sbx_agent_arg: str
    kit_ref: str                      # Bundled kit name (e.g. "omp") or remote URL
    sbx_secret_services: Tuple[str, ...] = ()
    signature_files: Tuple[str, ...] = ()

AGENT_SPECS: Dict[str, AgentSpec] = {
    "agy": AgentSpec(
        identifier="agy",
        display_name="Google Antigravity",
        sbx_agent_arg="gemini",
        kit_ref="git+https://github.com/shelajev/agy-sbx-kit.git",
        sbx_secret_services=("google",),
        signature_files=("antigravity.yaml",),
    ),
    "claude": AgentSpec(
        identifier="claude",
        display_name="Anthropic Claude Code",
        sbx_agent_arg="claude",
        kit_ref="claude",
        sbx_secret_services=("anthropic",),
        signature_files=("CLAUDE.md", ".claude"),
    ),
    "opencode": AgentSpec(
        identifier="opencode",
        display_name="OpenCode CLI",
        sbx_agent_arg="opencode",
        kit_ref="opencode",
        sbx_secret_services=("openrouter", "anthropic", "openai", "google"),
        signature_files=("opencode.json", ".opencode"),
    ),
    "codex": AgentSpec(
        identifier="codex",
        display_name="OpenAI Codex CLI",
        sbx_agent_arg="codex",
        kit_ref="codex",
        sbx_secret_services=("openai",),
        signature_files=(".codex",),
    ),
    "omp": AgentSpec(
        identifier="omp",
        display_name="Oh My Pi Harness",
        sbx_agent_arg="shell",
        kit_ref="omp",
        signature_files=(),
    ),
    "shell": AgentSpec(
        identifier="shell",
        display_name="Interactive Shell",
        sbx_agent_arg="shell",
        kit_ref="shell",
        signature_files=(),
    ),
}
```

---

## 2. CLI Flags & Explicit Configuration API

### A. Initialization (`init`)
- **`--agent <primary>`**: Selects primary running agent binary (default: `agy`).
- **`--with-agent <secondary>`**: Includes additional agent kits in the environment (e.g., `--with-agent opencode`). Can be repeated.
- **`--with-kit <kit>`**: Includes dev tool mixin kits (e.g., `--with-kit chrome-devtools`). Can be repeated.

Example initialization:
```bash
agy-sandbox init --agent claude --with-agent opencode --with-kit chrome-devtools
```

### B. Generated `agy.yaml`
```yaml
profile: default
project_name: my_app
agent: claude            # Primary active starting agent

build_args:
  PYTHON_VERSION: "3.11"
  NODE_VERSION: "20"
  RUST_VERSION: "stable"
  MOJO_VERSION: "latest"

sbx:
  enabled: true
  clone: true

  # EXPLICIT KITS: Ground truth list of all included agent & tool kits
  kits:
    - claude
    - opencode
    - chrome-devtools
```

### C. Post-Init CLI Modifications
- `agy-sandbox agents list` / `agy-sandbox agents add <agent>`
- `agy-sandbox kits list` / `agy-sandbox kits add <kit>`

---

## 3. Single Cold Execution Path (`agy-sandbox up`)

When `agy-sandbox up` runs, `SbxRunner` executes the single, non-magical path:

1. Reads `config.agent` (`claude`) and `config.sbx.kits` (`["claude", "opencode", "chrome-devtools"]`).
2. Calls `validate_and_resolve_kits(config.sbx.kits)`.
   - Checks `AGENT_SPECS`: if kit name matches an agent spec, uses `spec.kit_ref`.
   - Checks local bundled `kits/` directory (for `chrome-devtools`, `mojo-stdlib`, `omp`).
   - Passes remote git URLs (`git+https://...`) through as-is.
   - Fail-fast validation: raises `ValueError` if a kit name cannot be resolved.
3. Constructs and executes the single unified command:
   ```bash
   sbx run claude --kit claude --kit opencode --kit /path/to/chrome-devtools
   ```

---

## 4. Technical Implementation Steps

1. **`src/agy_sandbox/constants.py`**:
   - Add `AgentSpec` dataclass and data-driven `AGENT_SPECS` dictionary with canonical `kit_ref` entries.
2. **`src/agy_sandbox/naming.py`**:
   - Add `SandboxNamingResolver` producing profile-isolated sandbox names (`agy-sandbox-<project>--<profile>`).
3. **`src/agy_sandbox/kits.py`**:
   - Update `validate_and_resolve_kits()` to resolve agent kit references via `AGENT_SPECS`, local bundled directories, or remote URLs. Raise fast `ValueError` on unresolved kit names.
4. **`src/agy_sandbox/config.py`**:
   - Update `resolve_sbx_params()` to accept `agents` (from `--with-agent`) and `kits` (from `--with-kit`), resolving all explicit kits into `sbx.kits`.
5. **`src/agy_sandbox/git_guardian.py`**:
   - Extend `check_for_unsaved_sandbox_work()` to check for active session auth files (`.claude.json`, `.config/opencode/auth.json`) before destructive rebuild or stop operations, prompting interactive user override confirmation (`[y/N]`).
6. **`src/agy_sandbox/cli.py`**:
   - Add `--with-agent` flag to `init`.
   - Add `agy-sandbox agents list` and `agy-sandbox agents add <name>` CLI subcommands.
7. **`src/agy_sandbox/runners/sbx.py`**:
   - Implement `check_sbx_availability()` preflight check.
   - Execute `sbx run <agent> --name <sandbox_name> --kit <kit1> --kit <kit2> ...`.
8. **`tests/`**:
   - Add unit tests for `--with-agent` flag resolution, `AGENT_SPECS` kit resolution, interactive auth safeguards, and explicit `agy.yaml` generation.
