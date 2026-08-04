```text
    ___   ________  __    _____                 ____                
   /   | / ____/\ \/ /   / ___/____ _____  ____/ / /_  ____  _  __
  / /| |/ / __   \  /    \__ \/ __ `/ __ \/ __  / __ \/ __ \| |/_/
 / ___ / /_/ /   / /    ___/ / /_/ / / / / /_/ / /_/ / /_/ />  <  
/_/  |_\____/   /_/    /____/\__,_/_/ /_/\__,_/_.___/\____/_/|_|  
```

# agy-sandbox

A lightweight CLI tool that runs Google Antigravity and your project inside an isolated, containerized Docker environment. It supports dynamic language runtimes (Python, Node.js, Rust, Modular Mojo), Docker Sandboxes (`sbx`), OMP harness integration, and secure, profile-isolated Google logins.

## Prerequisites

- **Docker:** Must be installed and running on your host machine.
- **uv:** For installing the Python CLI tool globally.

## Installation

Install the CLI globally directly from this repository using `uv`:

```bash
uv tool install -e .
```

## Usage & Commands

- `agy-sandbox init`  
  Generates a default `agy.yaml` configuration file in your project workspace.  
  Options:
  - `--sbx`: Enable Docker Sandboxes (`sbx`) mode.
  - `--omp`: Configure the OMP (Oh My Pi) harness.
  - `--clone`: Enable isolated git clone mode inside the sandbox.
  - `--dockerfile`: Scaffold a local, customizable `Dockerfile.agy`.
  - `--with-kit <kit>`: Seed a bundled kit (e.g. `chrome-devtools`, `mojo-stdlib`).

- `agy-sandbox auto-init`  
  Scans project files (such as `Cargo.toml`, `pyproject.toml`, `package.json`, `pixi.toml`, `*.mojo`) and generates a tailored `agy.yaml`. Accepts `--sbx`, `--omp`, `--clone`, `--agent <agent>`, and `--with-kit <kit>`.

- `agy-sandbox up`  
  Launches the sandbox container and opens an interactive terminal session.  
  Use `--rebuild` to force rebuild custom images or sandboxes.

- `agy-sandbox down`  
  Stops and removes the running sandbox container for the current project.

- `agy-sandbox update-base`  
  Pulls the latest Antigravity engine and CLI from Google and bakes them into `agy-base:latest`.

- `agy-sandbox kits list`  
  Lists all available bundled mixin kits.

- `agy-sandbox kits add <name>`  
  Adds a bundled kit (e.g., `chrome-devtools`, `mojo-stdlib`, `omp`) to your project's `agy.yaml`.

## Configuration (`agy.yaml`)

Your sandbox environment is configured via `agy.yaml` in your project root:

```yaml
profile: default
project_name: my_app
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
  enabled: false
  agent: agy
  clone: false
  kits: []
```

### Language Runtimes (`build_args`)
- **`PYTHON_VERSION`**: Python version installed via `uv`.
- **`NODE_VERSION`**: Node.js version installed via NodeSource.
- **`RUST_VERSION`**: Rust toolchain (`cargo`, `rustc`, `rustup`) version.
- **`MOJO_VERSION`**: Modular Mojo & MAX platform version installed via `pixi` and Modular Conda channels. Set to `"none"` to skip.

## Bundled Kits

- **`chrome-devtools`**: Spawns headless Chromium with the `chrome-devtools-mcp` server for browser automation and debugging.
- **`omp`**: Terminal AI coding agent and tool harness (Oh My Pi).
- **`mojo-stdlib`**: Installs Bazelisk/Bazel launcher and LLVM `lit` test runner for compiling and testing the `modularml/mojo` standard library.

## 🔐 Multiple Isolated Logins

`agy-sandbox` manages isolated Antigravity logins across projects without frequent logging in and out:
- Each project sets its `profile` in `agy.yaml`.
- The sandbox runs an isolated headless Linux keyring database.
- Tokens are encrypted and stored under `~/.gemini_<profile>` on your host.
- Running `agy-sandbox up` authenticates you as the correct user for that project.
- Set `use_native_login: true` to share your host's active `~/.gemini` folder directly.

## Architecture

1. **`Dockerfile.base`**: Static base image caching the Antigravity engine, C build tools (`build-essential`, `pkg-config`, `libssl-dev`), terminal definitions, and base setup.
2. **`Dockerfile.default`**: Dynamic layer installing configured language runtimes (Python, Node.js, Rust, Pixi/Mojo).
3. **`Dockerfile.omp`**: Multi-arch base layer supporting OMP harness binaries (`arm64` and `x64`).
4. **Runner Architecture**: Modular `DockerRunner` and `SbxRunner` engines handling standard Docker containers and Docker Sandboxes (`sbx`).

## Contributing

1. Clone this repository.
2. Make changes under `src/`.
3. Run `uv tool install -e .` to apply local edits.
4. Run unit tests with `python -m unittest discover tests` and linter with `uv run ruff check .`.

## License

MIT License - see [LICENSE](LICENSE) for details.
