# Architecture & Code Quality Cleanup Plan

## Objective
Transform `agy-sandbox` from a procedural Python script wrapper into a modular, strongly typed, and testable SDK/CLI architecture.

---

## Key Refactoring Goals

### 1. Unified Constants & Zero Magic Strings (`src/agy_sandbox/constants.py`)
- Standardize all string literals, URLs (`DEFAULT_SBX_KIT_URL`), agent names (`AGENT_AGY`, `AGENT_SHELL`), kit names (`KIT_OMP`), and container paths (`/root/.gemini`, `/root/.config/gemini`).
- Eliminate all inline string literals across `cli.py`, `config.py`, `engine.py`, and `kits.py`.

### 2. Robust Domain Model & Slugification (`src/agy_sandbox/config.py`)
- Replace loose `.replace("_", "-")` string hacks with a proper `slugify_project_name()` function (handling spaces, underscores, uppercase, special characters).
- Type-annotate and validate `AgyConfig` fields cleanly.

### 3. Separation of Concerns & CLI Engine Decoupling
Split the 400-line procedural `engine.py` into focused, single-responsibility modules:

- **`src/agy_sandbox/runners/sbx.py` (`SbxRunner`)**:
  Encapsulates Docker Sandboxes daemon commands (`run`, `stop`, `rm`, `template load`).

- **`src/agy_sandbox/runners/docker.py` (`DockerRunner`)**:
  Encapsulates Docker CLI operations (`build`, `run`, `save`).

- **`src/agy_sandbox/git_guardian.py` (`GitGuardian`)**:
  Encapsulates safety checks scanning for dirty files, unpushed commits, and sandbox-only local branches before destroying a cloned sandbox.

### 4. Prompt Builder Abstraction (`src/agy_sandbox/prompts.py`)
- Extract LLM prompt construction logic for `auto-init` into `build_auto_init_prompt(...)`.
- Keeps `cli.py` ultra-thin (handling only argument parsing and high-level routing).

### 5. Shell Startup Script Extraction
- Extract the inline 30-line `startup_script` string (with `gnome-keyring-daemon` and `keyrings` logic) out of Python code into a dedicated package asset script or clean helper.

---

## File Structure After Refactoring
```
src/agy_sandbox/
├── __init__.py
├── cli.py               # Thin argument parser and command router
├── config.py            # AgyConfig, SbxConfig models, slugification, YAML I/O
├── constants.py         # Centralized system constants
├── git_guardian.py      # Safety checks for unsaved/unpushed sandbox work
├── kits.py              # Kit discovery, resolution, and spec validation
├── prompts.py           # Auto-init LLM prompt builder
└── runners/
    ├── __init__.py
    ├── docker.py        # Docker Engine image build/run operations
    └── sbx.py           # Docker Sandboxes daemon operations
```

---

## Verification & Safety Loop
1. Run `uv run ruff check .` to verify clean imports and zero lint/type errors.
2. Execute test CLI commands (`init`, `auto-init`, `kits list`, `up`, `down`) to confirm zero regressions in functionality.
