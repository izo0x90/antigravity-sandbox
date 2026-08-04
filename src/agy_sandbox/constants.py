import re

# File & Directory Names
DEFAULT_CONFIG_FILE = "agy.yaml"
LOCAL_DOCKERFILE_NAME = "Dockerfile.agy"
DEFAULT_DOCKERFILE_NAME = "Dockerfile.default"
BASE_DOCKERFILE_NAME = "Dockerfile.base"
OMP_DOCKERFILE_NAME = "Dockerfile.omp"

# Image Tags
DEFAULT_BASE_IMAGE = "agy-base:latest"
OMP_BASE_IMAGE = "agy-base-omp:latest"

# Agent Targets
AGENT_AGY = "agy"
AGENT_SHELL = "shell"
AGENT_OMP = "omp"

# Kit Names & Remote URLs
KIT_OMP = "omp"
DEFAULT_SBX_KIT_URL = "git+https://github.com/shelajev/agy-sbx-kit.git"

# Container Paths & Mount Points
CONTAINER_GEMINI_HOME = "/root/.gemini"
CONTAINER_GEMINI_CONFIG = "/root/.config/gemini"
CONTAINER_KEYRINGS = "/root/.local/share/keyrings"

# Terminal Support Defaults
DEFAULT_TERM = "xterm-256color"
DEFAULT_COLORTERM = "truecolor"
SUPPORTED_TERMINALS = {
    "xterm",
    "xterm-color",
    "xterm-256color",
    "screen",
    "screen-256color",
    "tmux",
    "tmux-256color",
    "xterm-kitty",
    "alacritty",
    "ghostty",
    "wezterm",
}


def slugify_project_name(name: str) -> str:
    """
    Converts arbitrary project names into valid, clean Docker/DNS-compatible slugs.
    Handles spaces, underscores, uppercase letters, and special characters.
    """
    if not name:
        return "default-project"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug if slug else "default-project"
