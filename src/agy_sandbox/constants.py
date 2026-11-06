import re

# Files
DEFAULT_CONFIG_FILE = "agy.yaml"
LOCAL_DOCKERFILE_NAME = "Dockerfile.agy"
DEFAULT_DOCKERFILE_NAME = "Dockerfile.default"

# Host-side state; see paths.py for everything derived from it
STATE_DIR = "~/.agy-sandbox"

# The image every project workload builds on unless agy.yaml says otherwise:
# Docker's agentless sandbox base (Debian, bash, git, curl, gh, jq, python, uv, docker).
DEFAULT_BASE_IMAGE = "docker.io/sbx/shell-image:latest"

# Login modes. sbx's own terms: a sandbox-local login is held by the agent
# inside the VM; a proxy-managed one lives in `sbx secret` on the host and the
# sandbox proxy injects it. The switch is which kits are attached: a bundled
# `<kit>-credentials` kit is attached next to `<kit>` only in proxy-managed mode.
AUTH_MODE_SANDBOX_LOCAL = "sandbox-local"
AUTH_MODE_PROXY_MANAGED = "proxy-managed"
AUTH_MODES = (AUTH_MODE_SANDBOX_LOCAL, AUTH_MODE_PROXY_MANAGED)
LEGACY_AUTH_MODES = {
    "sbx_persistent": AUTH_MODE_SANDBOX_LOCAL,
    "sbx_proxy": AUTH_MODE_PROXY_MANAGED,
}
CREDENTIALS_KIT_SUFFIX = "-credentials"

# v3 kit descriptors
KIT_DESCRIPTOR_SYNTAX = "# syntax=docker/sandbox-kit:3"
KIT_SCHEMA_VERSION = "3"
WORKLOAD_KIT_NAME = "workload"
REMOTE_KIT_SCHEMES = ("git+", "git@", "https://", "http://", "oci://", "docker.io/", "ghcr.io/")

# Bundled kit names referenced by code
KIT_CHROME_DEVTOOLS = "chrome-devtools"
KIT_CHROME_HOST = "chrome-host"

# `agy-sandbox recreate` snapshots the sandbox as <sandbox>:<RECREATE_TAG_PREFIX><timestamp>
RECREATE_TAG_PREFIX = "recreate-"

# sbx versions: in-place upgrades need `sbx kit add` with startup hooks (0.48)
MIN_SBX_VERSION_FOR_UPGRADE = (0, 48, 0)
PLAN_SCHEMA_VERSION = 1

# Host browser (Chrome on the host, driven from the sandbox via kits/chrome-host)
DEFAULT_HOST_BROWSER_PORT = 9222
HOST_BROWSER_STARTUP_TIMEOUT = 15

# Timeouts (seconds)
DEFAULT_CMD_TIMEOUT = 10
REMOVE_CMD_TIMEOUT = 15
# `sbx exec` starts a stopped sandbox first, which runs every kit's startup hooks
SANDBOX_EXEC_TIMEOUT = 300
# Builds, snapshots and copies scale with the sandbox; no timeout, only stdin closed
LONG_TIMEOUT = None
DAEMON_PREFLIGHT_TIMEOUT = 5
BANNER_WIDTH = 78
EXIT_CODE_INTERRUPTED = 130

# Default config values
SUPPORTED_BUILD_ARGS = ("PYTHON_VERSION", "NODE_VERSION", "RUST_VERSION", "MOJO_VERSION")
DEFAULT_BUILD_ARGS: dict = {}
DEFAULT_SETUP_SCRIPTS: list = []
DEFAULT_ENV_VARS = ["ENVIRONMENT=development"]


def slugify_project_name(name: str) -> str:
    """Turns an arbitrary project name into a Docker/DNS-safe slug."""
    if not name:
        return "default-project"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or "default-project"
