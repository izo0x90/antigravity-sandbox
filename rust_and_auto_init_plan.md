# Implementation Plan: First-Class Rust Runtime & Enhanced `auto-init`

## Overview
This plan introduces:
1. First-class Rust language runtime support (`cargo`, `rustc`, `rustup`) directly in `Dockerfile.default` alongside Python (`uv`) and Node.js.
2. C build tools (`build-essential`, `pkg-config`, `libssl-dev`) in `Dockerfile.base` for native Rust crate compilation (`openssl-sys`, C bindings).
3. Enhanced `auto-init` CLI flags (`--sbx`, `--omp`, `--clone`, `--agent`, `--with-kit`) and codebase auto-detection (`Cargo.toml`, `pyproject.toml`, `package.json`).

---

## File Changes & Details

### 1. `src/agy_sandbox/docker/Dockerfile.base`
- Add `build-essential`, `pkg-config`, and `libssl-dev` to the `apt-get install` list.
- **Why**: Essential for compiling native Rust crates and C bindings.

### 2. `src/agy_sandbox/docker/Dockerfile.default`
- Add build arg: `ARG RUST_VERSION=stable`.
- Add global environment variables:
  `ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo PATH=/usr/local/cargo/bin:$PATH`
- Run standard rustup installer into `/usr/local` with `--profile minimal` and grant write permissions (`chmod -R a+w /usr/local/cargo /usr/local/rustup`).

### 3. `src/agy_sandbox/config.py`
- Update default `build_args` dictionary in `write_default_config()` to include `"RUST_VERSION": "stable"`.

### 4. `src/agy_sandbox/cli.py`
- Add CLI arguments to `auto_init_parser`: `--sbx`, `--omp`, `--clone`, `--agent`, `--with-kit`.
- Update `auto_init_command()` prompt generation to pass SBX/agent/kit constraints and instruct the analyzer to detect `Cargo.toml`/`.rs` for `RUST_VERSION` alongside Python and Node.

---

## Verification Plan
1. Run `uv run ruff check .` to ensure formatting and linting pass.
2. Test `agy-sandbox init` and check generated `agy.yaml`.
3. Test `agy-sandbox auto-init --omp --with-kit chrome-devtools`.
