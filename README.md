```text
    ___   ________  __    _____                 ____                
   /   | / ____/\ \/ /   / ___/____ _____  ____/ / /_  ____  _  __
  / /| |/ / __   \  /    \__ \/ __ `/ __ \/ __  / __ \/ __ \| |/_/
 / ___ / /_/ /   / /    ___/ / /_/ / / / / /_/ / /_/ / /_/ />  <  
/_/  |_\____/   /_/    /____/\__,_/_/ /_/\__,_/_.___/\____/_/|_|  
```

# agy-sandbox

The quickest way to spin up sandboxed development environments for terminal AI coding agents on
Docker Sandboxes (`sbx`), with your toolchains and agent harnesses pre-installed.

`agy-sandbox` builds a custom Docker image for your project, loads it into `sbx` as a template, and
runs it as an isolated microVM with one or more agent harnesses installed side by side:

- **Custom images in sandboxes**: `Dockerfile.agy` (or the default) with any native dependencies,
  runtimes and system packages, run inside an `sbx` microVM.
- **Every agent, the same way**: Google Antigravity, Claude Code, OpenCode, Codex, Oh My Pi and
  Prime Agent are each a stacked image layer plus a bundled kit on `sbx`'s shell sandbox. Install
  one or five; they all launch identically.
- **One login switch**: `auth_mode: sandbox-local` (log in inside the sandbox) or
  `auth_mode: proxy-managed` (credentials stay on the host in `sbx secret`, the sandbox proxy
  injects them).
- **Ports, host browser, guardian**: publish sandbox ports to the host, let agents drive a Chrome
  on your machine, and get warned before destroying a sandbox with unsaved work or live logins.

## Prerequisites

- **Docker Desktop** with Docker Sandboxes (`sbx`) enabled and `sandboxd` running.
- **uv** for installing the CLI.

## Installation

```bash
uv tool install -e .
```

## Quick start

```bash
# Inspect the project read-only and write agy.yaml (falls back to an offline manifest scan)
agy-sandbox auto-init --agent claude,opencode

# Build the image, load it into sbx, create the sandbox and attach
agy-sandbox up

# Later: resume, publishing a port and using a Chrome on the host
agy-sandbox up -p 3000:3000 --host-browser

# Stop and remove (the guardian checks for unsaved work first)
agy-sandbox down
```

## Configuration (`agy.yaml`)

```yaml
profile: default
project_name: my_app
agent: claude                  # primary agent; its kit is always attached
kits:                          # agent kits and mixin kits (bundled names, local dirs or git+/oci refs)
  - claude
  - opencode
  - chrome-devtools
auth_mode: sandbox-local       # or proxy-managed
clone: true                    # work on a private in-VM clone instead of a bind mount
ports:                         # published to the host (sbx port spec)
  - "3000:3000"
host_browser:                  # optional; `up --host-browser` turns it on for one run
  enabled: false
  port: 9222
  headless: false
kit_args:                      # optional; passed through to `sbx run --kit-arg`
  agy-proxy.auth_method: gcp
build_args:
  PYTHON_VERSION: "3.12"
  NODE_VERSION: "20"
setup_scripts:                 # run once inside the VM when the sandbox is created
  - uv sync
env:                           # applied to every session
  - ENVIRONMENT=development
```

`env` values are passed to `sbx run -e`, so changing them takes effect on the next `up` without a
rebuild. `setup_scripts` become a generated mixin kit whose install hook runs them in the workspace
when the sandbox is created, so changing them needs `up --rebuild`.

### Upgrading a pre-0.5 `agy.yaml`

Older files nested most settings under `sbx:` and carried `use_native_login`, `workspace_path` and
`auth_mode: sbx_persistent | sbx_proxy`. They still load; every command prints a warning naming what
changed, and this rewrites the file in the current layout:

```bash
agy-sandbox config migrate
```

| Before (0.4)                   | Now (0.5)                                          |
| :----------------------------- | :------------------------------------------------- |
| `sbx.enabled: true/false`      | gone; `sbx` is the only runtime                    |
| `sbx.agent`, `sbx.kits`, `sbx.clone`, `sbx.ports`, `sbx.host_browser` | top-level `agent`, `kits`, `clone`, `ports`, `host_browser` |
| `use_native_login`             | gone; see `auth_mode`                              |
| `auth_mode: sbx_persistent`    | `auth_mode: sandbox-local`                         |
| `auth_mode: sbx_proxy`         | `auth_mode: proxy-managed`                         |
| `workspace_path`               | gone; `sbx` mounts the workspace at its host path  |
| `init --sbx`, `auto-init --sbx` | gone                                              |

## Login modes

Both modes use the same mechanism for every agent: the wrapper passes `auth_mode` to each bundled
agent kit as a kit argument, and the kit's setup hook does the right thing for its agent.

**`sandbox-local`** (default). You log in inside the sandbox (`agy`, `claude /login`, `codex`,
`opencode auth login`), exactly as on a normal machine. The real token lives in the sandbox and
nothing intercepts it. GCP / Gemini Enterprise logins for Antigravity work because the kit's network
policy includes the license host. Kits never write `proxy-managed` placeholders in this mode, even
if you have global `sbx` secrets stored.

**`proxy-managed`**. Credentials live on the host in `sbx secret`; the sandbox only ever sees a
placeholder that the `sbx` proxy swaps for the real value on outbound requests. For Claude, Codex
and OpenCode this is `sbx`'s built-in credential proxy. For Antigravity, whose Google OAuth is not a
built-in `sbx` service, the wrapper attaches the bundled `agy-proxy` kit, which declares that
credential. `up` tells you which secrets are missing:

```bash
sbx secret set anthropic --sandbox agy-sandbox-my-app
sbx secret set google --sandbox agy-sandbox-my-app
```

The rule is generic: when `auth_mode` is `proxy-managed` and a bundled kit named `<kit>-proxy`
exists, it is attached next to `<kit>`.

## CLI reference

- `agy-sandbox init [--agent a,b] [--clone] [--auth MODE] [--dockerfile] [--with-kit KIT]`
  Writes a template `agy.yaml`. The first agent is primary; all listed agents are added as kits.
- `agy-sandbox auto-init [--analyzer AGENT] [same options as init]`
  Runs a host agent in read-only mode to infer `build_args`, `setup_scripts`, `env` and kits, falling
  back to an offline manifest scanner. Flags you pass win over what the analyzer inferred.
- `agy-sandbox up [--rebuild] [--host-browser] [-p HOST:SANDBOX]`
  Builds the image if needed, loads it into `sbx`, creates or resumes the sandbox and attaches.
  Ports are published on creation and reconciled with `sbx ports` on resume.
- `agy-sandbox down`
  Removes the sandbox after the guardian check, and stops a host browser it started.
- `agy-sandbox agents list` / `agy-sandbox kits list`
- `agy-sandbox agents add NAME` / `agy-sandbox kits add NAME`
  The same operation: an agent is a kit named after it. Adding `shell` only sets the primary agent.
- `agy-sandbox config migrate`
  Rewrites a pre-0.5 `agy.yaml` in the current layout.
- `agy-sandbox update-base`
  Rebuilds `agy-base:latest` from scratch, plus the current project's agent layers, so new CLI
  releases are picked up.

## Agent harnesses

| Identifier    | Display name            | Image layer           | Kit             | Proxy services (proxy-managed)            |
| :------------ | :---------------------- | :-------------------- | :-------------- | :---------------------------------------- |
| `agy`         | Google Antigravity      | `Dockerfile.agy`      | `agy` (+ `agy-proxy`) | `google` via `agy-proxy`            |
| `claude`      | Anthropic Claude Code   | `Dockerfile.claude`   | `claude`        | `anthropic`                               |
| `opencode`    | OpenCode CLI            | `Dockerfile.opencode` | `opencode`      | `openrouter`, `anthropic`, `openai`, `google` |
| `codex`       | OpenAI Codex CLI        | `Dockerfile.codex`    | `codex`         | `openai`                                  |
| `omp`         | Oh My Pi Harness        | `Dockerfile.omp`      | `omp`           | none                                      |
| `prime-agent` | Prime Agent (RLM)       | `Dockerfile.prime`    | `prime-agent`   | `prime`, `anthropic`, `openai`, `google`  |
| `shell`       | Interactive Shell       | none                  | none            | none                                      |

Images stack as `Dockerfile.base` → one `Dockerfile.<agent>` per requested agent → the project's
`Dockerfile.agy` (or the packaged default), and the result is loaded into `sbx` as the template.

## Bundled mixin kits

- **`chrome-devtools`**: headless Chromium plus the `chrome-devtools-mcp` server for browser automation.
- **`chrome-host`**: added by `up --host-browser`; relays `127.0.0.1:9222` in the sandbox to a Chrome on the host.
- **`mojo-stdlib`**: Bazelisk and LLVM `lit` for `modularml/mojo` standard library work.
- **`agy-proxy`**: Antigravity's proxy-managed Google OAuth credential; attached automatically.
- The agent kits above.

### Host browser

`agy-sandbox up --host-browser` starts Chrome on the host (or reuses one listening on the port) with
a dedicated profile under `~/.agy-sandbox/host-browser/<sandbox>/` and attaches the `chrome-host`
kit, so every harness reaches it at the same `127.0.0.1:9222` address as the in-sandbox Chromium.
Requires the `chrome-devtools` kit. Kits are fixed when a sandbox is created, so switching an
existing sandbox needs `--rebuild`.

## Code Guardian

Before `up --rebuild` or `down`, one `sbx exec` inside the sandbox reports login/session files for
every agent, and for clone-mode sandboxes any uncommitted files, unpushed commits and sandbox-only
branches. You are asked to confirm before anything is destroyed.

## License

MIT License - see [LICENSE](LICENSE) for details.
