# To-Do: Jinja2 Templating for Dynamic Dockerfiles

## Context
Currently, the `agy-sandbox` tool dynamically generates a project-specific Dockerfile by appending python strings together inside `engine.py` (specifically in the `generate_dockerfile` function). While this works well for simple installations of `python` and `node`, as the sandbox tool grows to support more complex runtime configurations, OS packages, and environment setups, building Dockerfile strings manually will become messy and difficult to maintain.

To future-proof the tool, we should refactor `engine.py` to use a dedicated templating engine like Jinja2. This allows us to maintain a clean `.j2` template file where the Dockerfile logic is cleanly separated from the Python build logic.

Below is a starter plan for how a future contributor can implement this refactor.

## Implementation Starter Plan

### Step 1: Add Dependency

Add `Jinja2` to your `pyproject.toml` dependencies:

```toml
[project]
# ...
dependencies = [
    "PyYAML>=6.0.1",
    "Jinja2>=3.1.2",
]
```

### Step 2: Create the Template File

Create a new file at `docker/Dockerfile.dynamic.j2` with the following content:

```dockerfile
FROM agy-base:latest

{% if config.runtime.apt_packages %}
RUN apt-get update && apt-get install -y {{ config.runtime.apt_packages | join(' ') }} && rm -rf /var/lib/apt/lists/*
{% endif %}

{% if config.runtime.python %}
RUN uv python install {{ config.runtime.python }}
ENV PATH="/root/.local/share/uv/python/cpython-{{ config.runtime.python }}-linux-x86_64-gnu/bin:$PATH"
{% endif %}

{% if config.runtime.node %}
RUN curl -fsSL https://deb.nodesource.com/setup_{{ config.runtime.node }}.x | bash - && apt-get install -y nodejs
{% endif %}
```
*(Note: As of the recent architectural change, `setup_scripts` are executed dynamically at container boot via `dbus-run-session` in `engine.py`, so they are no longer part of the static Dockerfile build process and thus do not need to be in this template.)*

### Step 3: Refactor `engine.py`

Update `src/agy_sandbox/engine.py` to use Jinja2. Replace the `generate_dockerfile` function:

```python
import os
from jinja2 import Environment, FileSystemLoader
from .config import AgyConfig

def generate_dockerfile(config: AgyConfig) -> str:
    base_dir = get_base_dir()
    template_dir = os.path.join(base_dir, "docker")
    
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("Dockerfile.dynamic.j2")
    
    return template.render(config=config)
```
