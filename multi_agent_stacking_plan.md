# Multi-Harness Agent Stacking & Composable Layering Plan (v0.2.2)

## 📌 Executive Objective
Eliminate mutually exclusive `if / elif` branching during Docker image construction in SBX mode. When multiple agent harnesses (e.g., `omp`, `prime-agent`, `opencode`) are specified in `agy.yaml` under `sbx.kits`, all requested agent binaries must be layered sequentially onto the container image so that every binary (`/usr/local/bin/omp`, `/usr/local/bin/prime-agent`, etc.) is available globally on `$PATH` inside the sandbox microVM.

---

## 🏛️ Architecture & Image Chaining Pipeline

```text
                  ┌─────────────────────────────────────┐
                  │          agy-base:latest            │
                  │ (Ubuntu 22.04 + uv + Antigravity)  │
                  └──────────────────┬──────────────────┘
                                     │
                        Is 'omp' in kits/agent?
                                     │
                       ┌─────────────┴─────────────┐
                       │ YES                       │ NO
                       ▼                           │
          ┌──────────────────────────┐             │
          │ Build agy-base-omp       │             │
          │ FROM agy-base:latest     │             │
          └────────────┬─────────────┘             │
                       └─────────────┬─────────────┘
                                     │
                    Is 'prime-agent' in kits/agent?
                                     │
                       ┌─────────────┴─────────────┐
                       │ YES                       │ NO
                       ▼                           │
          ┌──────────────────────────┐             │
          │ Build agy-base-prime     │             │
          │ FROM agy-base-omp:latest │             │
          └────────────┬─────────────┘             │
                       └─────────────┬─────────────┘
                                     │
                                     ▼
                  ┌─────────────────────────────────────┐
                  │    Final Chained Base Image         │
                  │  (Contains /usr/local/bin/omp       │
                  │     AND /usr/local/bin/prime)       │
                  └─────────────────────────────────────┘
```

---

## 🛠️ Implementation Breakdown

### 1. `src/agy_sandbox/docker/Dockerfile.omp` & `Dockerfile.prime`
- Support `ARG BASE_IMAGE=agy-base:latest` and `FROM ${BASE_IMAGE}` in both Dockerfiles.
- Allows `Dockerfile.prime` to build cleanly on top of `agy-base-omp:latest` (or any preceding base layer).

### 2. `src/agy_sandbox/runners/docker.py`
- Refactor `build_omp_base_image(base_image)` and `build_prime_base_image(base_image)` to accept an explicit `base_image` parameter and pass `--build-arg BASE_IMAGE={base_image}`.
- Refactor `build_project_image()` from exclusive `if / elif` to sequential chaining:
  ```python
  effective_base = DEFAULT_BASE_IMAGE
  if config.is_omp_requested:
      effective_base = cls.build_omp_base_image(base_image=effective_base)
  if config.is_prime_requested:
      effective_base = cls.build_prime_base_image(base_image=effective_base)
  ```
- Pass `BASE_IMAGE={effective_base}` when building the final project image (`Dockerfile.default` / `Dockerfile.agy`).

### 3. `src/agy_sandbox/runners/sbx.py` (SBX Secret Provisioning)
- Update `SbxRunner.ensure_secret_configured()` to iterate through **all** agent harnesses present in `sbx.kits` and `sbx.agent`:
  - Configures secrets for all requested agent specs (e.g. `prime`, `anthropic`, `openai`, `google`).

### 4. `tests/test_multi_agent_stacking.py`
- Add unit tests verifying:
  - When `kits: ["omp", "prime-agent"]` is configured, `effective_base` correctly chains both `OMP` and `PRIME` layers.
  - Proxy secrets for all requested agent kits in SBX mode are configured.

### 5. Version Bump & Release (`0.2.2`)
- Bump `pyproject.toml` to `0.2.2`.
- Run full test suite (`42+` tests).
- Commit, push to `origin/main`, and re-install globally via `uv tool install`.
