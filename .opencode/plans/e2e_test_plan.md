# Comprehensive End-to-End (E2E) Test Plan for `agy-sandbox`

## Objective
Thoroughly validate basic CLI functionality, configuration parsing, image build pipelines, environment lifecycle (creation, resuming, rebuilding, teardown), active session safeguards, and all 6 supported agent harnesses (`agy`, `claude`, `opencode`, `codex`, `omp`, `shell`).

---

## Phase 1: CLI Commands & Scaffolding (Isolated Test Workspaces)
*Objective: Verify that all CLI subcommands parse arguments, manage `agy.yaml`, and list agents/kits cleanly without launching containers.*

1. **Subcommand Listing Checks**:
   - `agy-sandbox agents list`: Verify output displays all 6 supported agent harnesses (`agy`, `claude`, `opencode`, `codex`, `omp`, `shell`).
   - `agy-sandbox kits list`: Verify output displays all bundled kits (`chrome-devtools`, `mojo-stdlib`, `omp`).

2. **`init` Scaffolding Matrix**:
   - **Default Init**: Run `agy-sandbox init` in an empty temp directory. Verify `agy.yaml` contains default `agent: agy`, `auth_mode: sbx_persistent`, and `sbx.enabled: false`.
   - **Multi-Agent & Kit Flags**: Run `agy-sandbox init --agent claude --with-agent opencode --sbx --clone --with-kit chrome-devtools`. Verify `agy.yaml` contains:
     - `agent: claude`
     - `sbx.enabled: true`
     - `sbx.clone: true`
     - `sbx.kits: ["claude", "opencode", "chrome-devtools"]`
   - **Custom Dockerfile Scaffolding**: Run `agy-sandbox init --dockerfile`. Verify `Dockerfile.agy` is created in the workspace directory.

3. **Config Mutation Commands (`agents add` & `kits add`)**:
   - `agy-sandbox agents add codex`: Verify `codex` kit reference is appended to `sbx.kits` in `agy.yaml`.
   - `agy-sandbox kits add omp`: Verify `omp` is appended to `sbx.kits` and `sbx.agent` is set to `omp`.

---

## Phase 2: Host Base & Project Image Construction
*Objective: Verify base and project Docker image building pipelines.*

1. **Base Image Build (`update-base`)**:
   - Run `agy-sandbox update-base`. Verify `agy-base:latest` Docker image is successfully built.
2. **Stacked OMP Base Image Build**:
   - Test `build_omp_base_image()`. Verify `agy-base-omp:latest` is built when OMP harness or kit is requested.
3. **Custom Project Image Build**:
   - Test `build_project_image()` with custom `Dockerfile.agy`. Verify build arguments (`PYTHON_VERSION`, `NODE_VERSION`, `RUST_VERSION`, `MOJO_VERSION`) pass correctly.

---

## Phase 3: Sandbox Lifecycle & Active Session Safeguards Across All 6 Harnesses
*Objective: Verify creation, secret proxy configuration, execution, resuming, active auth safeguards, and teardown across all supported agent harnesses.*

### Test Matrix

| Harness | Primary Agent | SBX Agent Arg | Proxy Secret Services | Session Auth Guard File |
| :--- | :--- | :--- | :--- | :--- |
| **1. Antigravity** | `agy` | `gemini` | `google` | `/root/.gemini` |
| **2. Claude Code** | `claude` | `claude` | `anthropic` | `/home/agent/.claude.json` |
| **3. OpenCode** | `opencode` | `opencode` | `openrouter`, `anthropic`, `openai`, `google` | `/home/agent/.config/opencode/auth.json` |
| **4. Codex** | `codex` | `codex` | `openai` | `/home/agent/.codex/auth.json` |
| **5. Oh My Pi** | `omp` | `shell` | *(none)* | `/home/agent/.omp` |
| **6. Interactive Shell** | `shell` | `shell` | *(none)* | *(cloned git changes)* |

### Execution Workflow for Each Harness Scenario:

1. **Sandbox Creation & Preflight**:
   - Verify `sbx` daemon preflight check (`check_sbx_availability`).
   - Verify SBX secret proxy configuration (`sbx secret set <sandbox_name> <service>`).
   - Verify `sbx run` is executed with proper `--name`, `--template`, `--kit`, `--clone`, and agent arguments.

2. **Resuming Existing Sandbox**:
   - Execute `up` a second time without `--rebuild`.
   - Verify existing sandbox container is resumed directly without re-exporting image tarballs.

3. **Code Guardian Active Auth Session Safeguard (`up --rebuild` / `down`)**:
   - Simulate active auth session file inside container (`.claude.json`, `.config/opencode/auth.json`, `.codex/auth.json`, `.gemini`, `.omp`).
   - Execute `up --rebuild` or `down`.
   - Verify Code Guardian detects session files and triggers the destruction warning prompt.

4. **Cloned Workspace Unsaved Work Safeguard**:
   - For `sbx.clone: true`, simulate uncommitted Git changes inside the microVM clone.
   - Execute `down`. Verify dirty files warning is displayed.

5. **Sandbox Teardown (`down`)**:
   - Confirm destruction prompt (or pass simulated approval).
   - Verify `sbx stop` and `sbx rm -f` remove the sandbox container cleanly.

---

## Phase 4: Non-Sandboxed Host Docker Mode (`sbx.enabled: false`)
*Objective: Verify standard Docker Engine container execution when Docker Sandboxes is disabled.*

1. **Configuration**:
   - Config with `sbx.enabled: false`, `profile: client_a`, `use_native_login: false`.
2. **Container Launch (`up`)**:
   - Verify `docker run` uses container name `agy-sandbox-container-<project>--client-a`.
   - Verify host profile directory bind-mount is created at `~/.gemini_client_a`.
3. **Container Stop (`down`)**:
   - Verify `docker stop` and `docker rm` stop the container cleanly.
