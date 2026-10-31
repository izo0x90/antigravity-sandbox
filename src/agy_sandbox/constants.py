import re

# Files
DEFAULT_CONFIG_FILE = "agy.yaml"
LOCAL_DOCKERFILE_NAME = "Dockerfile.agy"
DEFAULT_DOCKERFILE_NAME = "Dockerfile.default"
BASE_DOCKERFILE_NAME = "Dockerfile.base"
DEFAULT_BASE_IMAGE = "agy-base:latest"

# Host-side state (host browser profiles, generated per-project kits)
STATE_DIR = "~/.agy-sandbox"

# Login modes. These are sbx's own terms: a sandbox-local login is held by the
# agent inside the VM; a proxy-managed one lives in `sbx secret` on the host and
# the sandbox proxy injects it on outbound requests.
AUTH_MODE_SANDBOX_LOCAL = "sandbox-local"
AUTH_MODE_PROXY_MANAGED = "proxy-managed"
AUTH_MODES = (AUTH_MODE_SANDBOX_LOCAL, AUTH_MODE_PROXY_MANAGED)
LEGACY_AUTH_MODES = {
    "sbx_persistent": AUTH_MODE_SANDBOX_LOCAL,
    "sbx_proxy": AUTH_MODE_PROXY_MANAGED,
}
# The kit argument every bundled agent kit that cares about login mode declares.
AUTH_MODE_KIT_ARG = "auth_mode"
# A bundled kit `<name>-proxy` holds the proxy credential declaration for kit
# `<name>` and is attached only in proxy-managed mode.
PROXY_KIT_SUFFIX = "-proxy"

# sbx launches every sandbox as a plain shell; agents are mixin kits on top.
SBX_AGENT = "shell"
# The built-in shell sandbox injects these as `proxy-managed` placeholders for
# every service sbx knows, whether or not a secret is stored. In sandbox-local
# mode they are blanked on the session so no agent silently uses a host key.
PROXY_PLACEHOLDER_ENV = (
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "NEBIUS_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "XAI_API_KEY",
)
# `agy-sandbox upgrade` snapshots the sandbox as <image>:<UPGRADE_TAG_PREFIX><timestamp>
UPGRADE_TAG_PREFIX = "upgrade-"
UPGRADE_STAGING_DIR = f"{STATE_DIR}/upgrades"

# Bundled kit names referenced by code
KIT_CHROME_DEVTOOLS = "chrome-devtools"
KIT_CHROME_HOST = "chrome-host"
PROJECT_KIT_NAME = "project-setup"
REMOTE_KIT_SCHEMES = ("git+", "git@", "https://", "http://", "oci://")

# Host browser (Chrome on the host, driven from the sandbox via kits/chrome-host)
DEFAULT_HOST_BROWSER_PORT = 9222
HOST_BROWSER_STATE_DIR = f"{STATE_DIR}/host-browser"
HOST_BROWSER_STARTUP_TIMEOUT = 15

# Timeouts (seconds)
DEFAULT_CMD_TIMEOUT = 10
REMOVE_CMD_TIMEOUT = 15
# `sbx exec` starts a stopped sandbox first, which runs every kit's startup hooks
SANDBOX_EXEC_TIMEOUT = 300
# Snapshots and file copies scale with the sandbox; no timeout, only stdin closed
SNAPSHOT_TIMEOUT = None
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
