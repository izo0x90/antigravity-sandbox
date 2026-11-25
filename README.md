```text
    ___   ________  __    _____                 ____                
   /   | / ____/\ \/ /   / ___/____ _____  ____/ / /_  ____  _  __
  / /| |/ / __   \  /    \__ \/ __ `/ __ \/ __  / __ \/ __ \| |/_/
 / ___ / /_/ /   / /    ___/ / /_/ / / / / /_/ / /_/ / /_/ />  <  
/_/  |_\____/   /_/    /____/\__,_/_/ /_/\__,_/_.___/\____/_/|_|  
```

# agy-sandbox

The quickest way to spin up sandboxed development environments for terminal AI coding agents on
Docker Sandboxes (`sbx`), with your project's toolchain and one or more agents pre-installed.

`agy-sandbox` turns one `agy.yaml` into a native `sbx` composition: a generated **workload kit**
built from your `Dockerfile.agy` (or the packaged default) and one **mixin kit** per agent, all in
the v3 kit format `sbx` builds and runs itself.

- **Project images without a pipeline**: `Dockerfile.agy` plus `build_args` become a v3 workload
  kit on `docker.io/sbx/shell-image`; `sbx` builds it when the sandbox is created. No `docker build`,
  no `docker save`, no template store.
- **Every agent, the same way**: Google Antigravity, Claude Code, OpenCode, Codex, Oh My Pi and
  Prime Agent are each a bundled v3 mixin that carries its own binary. Install one or five.
- **One login switch**: `auth_mode: sandbox-local` (log in inside the sandbox; nothing is
  intercepted) or `auth_mode: proxy-managed` (credentials stay on the host in `sbx secret`; a
  bundled `<agent>-credentials` kit lets the sandbox proxy inject them).
- **Upgrade without losing anything**: `upgrade` adds kits, env and ports to a running sandbox in
  place; `recreate` rebuilds it from a snapshot of itself for the changes `sbx` cannot apply in place.
- **Ports, host browser, guardian**: publish sandbox ports, let agents drive a Chrome on your
  machine, and get warned before destroying a sandbox with unsaved work or live logins.

## Prerequisites

- **Docker Sandboxes CLI** `sbx` 0.50 or newer with its daemon running (`brew install docker/tap/sbx`).
  `upgrade` needs 0.48 or newer; everything else works on any `sbx` with v3 kit support (0.45+).
- **uv** for installing the CLI.
- Docker on the host is needed only if `sbx` on your machine builds local kits with the host
  engine; current releases build them in their own builder sandbox.

## Installation

```bash
uv tool install -e .
```

## Quick start

```bash
# Inspect the project read-only and write agy.yaml (falls back to an offline manifest scan)
agy-sandbox auto-init --agent claude,opencode

# Build the workload, create the sandbox and attach
agy-sandbox up

# Later: resume, publishing a port and using a Chrome on the host
agy-sandbox up -p 3000:3000 --host-browser

# Added a kit or an agent to agy.yaml? Apply it without touching what is inside
agy-sandbox upgrade

# Stop and remove (the guardian checks for unsaved work first)
agy-sandbox down
```

## Configuration (`agy.yaml`)

```yaml
profile: default
project_name: my_app
agent: claude                  # primary agent; its kit is always attached
kits:                          # agent kits and mixin kits (bundled names, local kit dirs, git+/oci/docker.io refs)
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
kit_args:                      # optional; passed through as `--kit-arg`
  agy-credentials.auth_method: gcp
base_image: docker.io/sbx/shell-image:latest   # optional; the workload's FROM
build_args:                    # ARGs of Dockerfile.agy / the default Dockerfile
  PYTHON_VERSION: "3.12"
  NODE_VERSION: "20"
setup_scripts:                 # run once at creation, as the agent user, in a login shell, in the workspace
  - uv sync
env:                           # applied to every session
  - ENVIRONMENT=development
```

`env` is passed to every session, so changing it takes effect on the next `up`. `build_args` and
`setup_scripts` are part of the generated workload kit, so changing them needs `up --rebuild` (or
`recreate`, which keeps the sandbox's contents but not a new image).

Files from 0.5 need no changes. Pre-0.5 files (with an `sbx:` block, `use_native_login`,
`workspace_path`, `sbx_persistent`/`sbx_proxy`) still load; every command prints what changed and
`agy-sandbox config migrate` rewrites them.

## How a sandbox is composed

```
agy.yaml ──> ~/.agy-sandbox/kits/<sandbox>/workload/      the generated v3 workload kit
             (workload.yaml + workload.dockerfile)        FROM base_image + your Dockerfile.agy
         ──> sbx create --name <sandbox> [--clone] (-e ..)* (-p ..)* (--kit <mixin>)* <workload> <workspace>
         ──> ~/.agy-sandbox/sandboxes/<sandbox>/plan.json  what the sandbox was built with
```

The mixins, in order: the primary agent's kit, every kit in `kits`, each followed by its
`<kit>-credentials` kit when `auth_mode` is `proxy-managed`, then `chrome-host` when the host browser
is on. The saved plan is how later runs know what changed (see Upgrading).

## Login modes

**`sandbox-local`** (default). You log in inside the sandbox (`agy`, `claude /login`, `codex`,
`opencode auth login`), exactly as on a normal machine. No kit declares a credential, so nothing is
intercepted and no `*_API_KEY` placeholder exists. GCP / Gemini Enterprise logins for Antigravity
work because the kit's network policy includes the license and Vertex hosts.

**`proxy-managed`**. Credentials live on the host in `sbx secret`; the sandbox only ever sees a
placeholder that the `sbx` proxy swaps for the real value on outbound requests. The wrapper attaches
the bundled `<agent>-credentials` kit next to each agent kit; they are declaration-only and vendored
from Docker's own kits. `up` tells you which secrets are missing:

```bash
sbx secret set anthropic --sandbox agy-sandbox-my-app
sbx secret set antigravity --sandbox agy-sandbox-my-app   # Antigravity's Google OAuth session
```

`sbx` allows one owner per credential service in a sandbox, so two agents that both own a provider
(Claude Code and OpenCode both own `anthropic`) cannot share a proxy-managed sandbox; `agy-sandbox`
says so before creating anything. Use `sandbox-local` for that combination.

## CLI reference

- `agy-sandbox init [--agent a,b] [--clone] [--auth MODE] [--dockerfile] [--with-kit KIT]`
  Writes a template `agy.yaml`. The first agent is primary; all listed agents are added as kits.
- `agy-sandbox auto-init [--analyzer AGENT] [same options as init]`
  Runs a host agent in read-only mode to infer `build_args`, `setup_scripts`, `env` and kits, falling
  back to an offline manifest scanner. Flags you pass win over what the analyzer inferred.
- `agy-sandbox up [--rebuild] [--host-browser] [-p HOST:SANDBOX] [--no-attach] [-y]`
  Writes the workload kit, creates the sandbox (sbx builds the image) or resumes it, and attaches.
  On resume it reports how `agy.yaml` differs from the sandbox and which command applies it.
- `agy-sandbox upgrade [--host-browser] [-p HOST:SANDBOX] [--no-attach]`
  Applies additions in place: new kits or agents (`sbx kit add`, binaries included), env, ports, the
  host browser. Refuses anything else and names the command to use.
- `agy-sandbox recreate [--as NAME] [--host-browser] [-p HOST:SANDBOX] [--no-attach] [-y]`
  Rebuilds the sandbox from a snapshot of its own filesystem, carrying kit volumes and the clone
  workspace across, for removed kits, changed kit arguments and a changed login mode.
  With `--as NAME` it builds a second sandbox called NAME beside this one and leaves the original
  untouched, so you can check the copy and remove the old one (`sbx rm`) when satisfied. A sandbox
  built by agy-sandbox 0.5 ran its sessions as root; the copy is converted to the agent user and the
  old root home is moved into `/home/agent`. Afterwards a full file-by-file comparison of the two
  sandboxes is written to `~/.agy-sandbox/recreate-<name>-<timestamp>.txt` and summarised, so you can
  confirm nothing was lost before you delete the original.
- `agy-sandbox down [-y]`
  Removes the sandbox after the guardian check, and stops a host browser it started.

`--no-attach` does the work and returns instead of opening the session; `-y` answers the guardian's
prompt. Both exist for scripts such as `scripts/live-check.sh`.
- `agy-sandbox agents list` / `agy-sandbox kits list`
- `agy-sandbox agents add NAME` / `agy-sandbox kits add NAME`
  The same operation: an agent is a kit named after it. Adding `shell` only sets the primary agent.
- `agy-sandbox config migrate`
  Rewrites a pre-0.5 `agy.yaml` in the current layout.

## Agent harnesses

| Identifier    | Display name            | Kit (overlay)                                      | Credentials kit (proxy-managed) |
| :------------ | :---------------------- | :------------------------------------------------- | :------------------------------ |
| `agy`         | Google Antigravity      | `agy` on `docker.io/sbx/antigravity-mixin`         | `agy-credentials`: `google`, `antigravity` (OAuth) |
| `claude`      | Anthropic Claude Code   | `claude` on `docker.io/sbx/claude-mixin`           | `claude-credentials`: `anthropic` (API key or OAuth) |
| `codex`       | OpenAI Codex CLI        | `codex` on `docker.io/sbx/codex-mixin`             | `codex-credentials`: `openai` (API key or OAuth) |
| `opencode`    | OpenCode CLI            | `opencode` on `docker.io/sbx/opencode-mixin`       | `opencode-credentials`: `anthropic`, `openai`, `google`, `groq`, `openrouter`, `xai`, `github` |
| `omp`         | Oh My Pi Harness        | `omp` (own overlay)                                | none |
| `prime-agent` | Prime Agent (RLM)       | `prime-agent` (own overlay)                        | none |
| `shell`       | Interactive Shell       | none                                               | none |

Agent kits never declare credentials themselves. Claude Code is installed by Anthropic's native
installer as the `agent` user, so `claude update` and auto-updates work inside the sandbox; codex,
opencode and agy are copied out of Docker's published mixin images at build time, so they track
Docker's releases without a build pipeline here. Pin Claude Code with `--kit-arg claude.version=x.y.z`
(sbx) or the kit's `version` argument.

## Bundled mixin kits

- **`chrome-devtools`**: headless Chromium plus the `chrome-devtools-mcp` server for browser automation.
- **`chrome-host`**: added by `up --host-browser`; relays `127.0.0.1:9222` in the sandbox to a Chrome on the host.
- **`mojo-stdlib`**: Bazelisk and LLVM `lit` for `modularml/mojo` standard library work.
- The agent kits above, and their `*-credentials` kits (attached by the tool, not listed by `kits list`).

Any other v3 kit works too: a local kit directory, `git+https://...#dir=...`, or an image reference
such as `docker.io/sbx/playwright:latest` in `kits:`.

### Host browser

`agy-sandbox up --host-browser` starts Chrome on the host (or reuses one listening on the port) with
a dedicated profile under `~/.agy-sandbox/host-browser/<sandbox>/` and attaches the `chrome-host`
kit, so every harness reaches it at the same `127.0.0.1:9222` address as the in-sandbox Chromium.
Requires the `chrome-devtools` kit. On an existing sandbox, `agy-sandbox upgrade --host-browser` adds it.

## Upgrading a sandbox without losing its contents

A long-running sandbox accumulates state that is not in any config: packages the agent installed,
tools it built, dotfiles it wrote. `up --rebuild` throws all of that away. The two commands below do not.

| Change in `agy.yaml` | Command | How |
| :-- | :-- | :-- |
| kit or agent added, `env`, `ports`, host browser turned on | `upgrade` | `sbx kit add` on the running sandbox; env on the session; `sbx port publish` |
| kit removed, kit argument changed, `auth_mode` changed, host browser turned off | `recreate` | snapshot the filesystem (`sbx template save`), copy kit volumes and the clone workspace out, remove, create from the snapshot, copy back |
| `Dockerfile.agy`, `build_args`, `setup_scripts`, `clone` | `up --rebuild` | a new image cannot be merged into an existing filesystem |

`recreate` keeps the snapshot as `<sandbox>:recreate-<timestamp>` until the next recreate, and prints
the exact `sbx create` command to restore from it if anything fails half way.

Sandboxes created by agy-sandbox 0.5 keep running and `up` resumes them, but they have no saved plan,
so `upgrade` refuses them. They also ran their sessions as root. To move one to 0.6 on the agent user
without losing its contents, use `recreate --as <new-name>`: it builds the upgraded sandbox beside the
old one, converts the user, migrates the old root home, and verifies the result, so you keep the
original until you have checked the copy. `recreate` without `--as` refuses a sandbox whose sessions
start as root (it would replace the sandbox in place and strand the root home) and points at `--as`.

## Code Guardian

Before `up --rebuild`, `recreate` or `down`, one `sbx exec` inside the sandbox reports login/session
files for every agent, and for clone-mode sandboxes any uncommitted files, unpushed commits and
sandbox-only branches. You are asked to confirm before anything is destroyed.

## Development

```bash
uv sync --group dev
.venv/bin/ruff check src tests
.venv/bin/python -m unittest discover -s tests -t .   # sbx and docker are mocked
scripts/validate-kits.sh                               # builds every kit descriptor through the v3 frontend (needs Docker)
scripts/live-check.sh                                  # live gates on a throwaway sandbox (needs sbx >= 0.50)
```

Host-side state lives under `~/.agy-sandbox` (`AGY_SANDBOX_STATE_DIR` overrides it).

## License

MIT License - see [LICENSE](LICENSE) for details.
