# Docker Sandboxes (sbx) Integration Plan for agy-sandbox

This document outlines the architectural plan to integrate native **Docker Sandboxes (`sbx`)** support into `agy-sandbox` in a clean, decoupled, and safe manner.

---

## 🎯 Objectives

1. **Declarative Integration**: Enable standard `agy-sandbox` environments to be natively orchestrated via `sbx` when specified in the configuration (`agy.yaml`).
2. **Offline Template Loading**: Bypass the need for a remote Docker registry by creating an offline image-to-template bridge between the host's Docker daemon and `sandboxd`'s isolated storage.
3. **High-Performance Caching**: Short-circuit image transfers by verifying SHA-256 IDs of already loaded templates to enable sub-second resume capabilities.
4. **🛡️ Data Loss Prevention (3-Tier Git Check)**: Safely inspect and alert users about uncommitted files, unpushed commits, or local-only branches inside `--clone` sandboxes before any destructive action is performed.

---

## 1. Declarative Configuration (`agy.yaml`)

We introduce a backward-compatible `sbx` block inside `agy.yaml`.

```yaml
profile: default_project
project_name: my_app
runtime:
  python: "3.11"
  node: "20"
  apt_packages:
    - build-essential

# New Configuration Namespace
sbx:
  enabled: true        # If true, agy-sandbox up/down uses Docker Sandboxes (sbx)
  agent: "agy"         # Target agent engine to run (default: "agy")
  clone: true          # Runs in-container Git clone mode for isolation
  kits:                # List of directories, URLs, or OCI images as kits
    - "git+https://github.com/shelajev/agy-sbx-kit.git"
    - "."              # Current directory as local mixin (enables spec.yaml)
```

---

## 2. Decoupled Runner Architecture

To keep the codebase modular, clean, and easily maintainable, the core `run_up` and `run_down` functions in `src/agy_sandbox/engine.py` will serve as clean orchestrators, delegating to single-responsibility sub-runners:

```python
def run_up(config: AgyConfig) -> None:
    # 1. Always build the custom host Docker image to compile custom runtimes
    image_name = build_project_image(config)
    
    # 2. Delegate execution based on configuration
    if config.sbx.enabled:
        run_up_sbx(config, image_name)
    else:
        run_up_docker(config, image_name)

def run_down(config: AgyConfig) -> None:
    if config.sbx.enabled:
        run_down_sbx(config)
    else:
        run_down_docker(config)
```

---

## 3. Zero-Push Offline Image Injection & Caching

Because `sandboxd` (the Docker Sandboxes daemon) runs in its own isolated runtime environment with its own image store, standard host Docker images are invisible to it. We solve this offline without registry credentials:

### Execution Flow in `run_up_sbx`:

1. **Smart Resume**: Query `sbx ls` first. If the sandbox `agy-sandbox-<project_name>` already exists, bypass the build, save, and load pipeline entirely and instantly attach via:
   ```bash
   sbx run --name agy-sandbox-<project_name>
   ```
2. **Template Validation**: Check if the template image `agy-sandbox-<project_name>:latest` is already loaded in `sbx template ls`. If the loaded template ID matches the host image ID, skip loading.
3. **Safe Export-Load Bridge**:
   If the template is missing or outdated, export it safely via a secure python context manager and import it directly into `sbx`:
   ```python
   fd, temp_tar = tempfile.mkstemp(suffix=".tar")
   os.close(fd)
   try:
       subprocess.run(["docker", "save", "-o", temp_tar, f"{image_name}:latest"])
       subprocess.run(["sbx", "template", "load", temp_tar])
   finally:
       os.unlink(temp_tar)  # Guarantees zero disk leak on crashes
   ```
4. **Boot Sandbox**:
   ```bash
   sbx run --name <name> --template <image_name>:latest [--kit <kit> ...] [--clone] <agent>
   ```

---

## 4. 🛡️ Bulletproof Git Safety Net (Anti-Vaporization)

When running in `--clone` mode, any edits, commits, or branch creations exist **only** inside the container volume. Destroying the sandbox with `agy-sandbox down` or a forced rebuild will permanently wipe this work.

Before any destructive action, `agy-sandbox` will programmatically execute a **3-Tier Git Audit** inside the running container using `sbx exec`:

1. **Tier 1: Dirty State**: `git status --porcelain` (Unsaved edits).
2. **Tier 2: Commits**: `git cherry -v` (Commits created inside the sandbox that are not pushed to host remote).
3. **Tier 3: Branches**: `git log --branches --not --remotes --oneline` (Container branches that do not exist on the remote host repository).

### Guard Logic:
* If *any* of the three checks return files, commits, or branches, `agy-sandbox` halts immediately.
* It prints a clean table of the unsaved files/commits to the console.
* It prompts the developer with a clear recommendation: *"To save your work, run `git push origin` inside the sandbox container first!"*
* It blocks execution unless the user explicitly inputs `y`/`Y` to authorize deleting the files anyway.

---

## 5. CLI Overrides & Rebuilds

* **`agy-sandbox up --sbx`**: Forces the tool to boot using Docker Sandboxes even if not enabled inside `agy.yaml`.
* **`agy-sandbox down --sbx`**: Cleans up and stops the Docker Sandboxes container safely.
* **`agy-sandbox up --rebuild`**: Forcefully destroys the existing sandbox (with Git safety verification) and rebuilds the custom base image.
