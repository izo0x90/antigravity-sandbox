from typing import List, Sequence

from .config import AgyConfig
from .constants import HOST_BROWSER_ENV_MARKER, KIT_CHROME_DEVTOOLS, KIT_CHROME_HOST
from .git_guardian import check_for_unsaved_sandbox_work
from .host_browser import HostBrowser, HostBrowserError
from .runners.docker import DockerRunner
from .runners.sbx import SbxRunner, remove_sandbox, sandbox_exists, sandbox_has_env


def build_base_image() -> None:
    DockerRunner.build_base_image()


def build_omp_base_image() -> None:
    DockerRunner.build_omp_base_image()


def build_prime_base_image() -> None:
    DockerRunner.build_prime_base_image()


def build_project_image(config: AgyConfig) -> str:
    return DockerRunner.build_project_image(config)


def run_up_sbx(
    config: AgyConfig,
    image_name: str,
    rebuild: bool = False,
    extra_kits: Sequence[str] = (),
    kit_args: Sequence[str] = (),
) -> None:
    SbxRunner.run_sandbox(config, image_name, rebuild=rebuild, extra_kits=extra_kits, kit_args=kit_args)


def run_up_docker(config: AgyConfig, image_name: str) -> None:
    DockerRunner.run_container(config, image_name)


def host_browser_for(config: AgyConfig) -> HostBrowser:
    settings = config.sbx.host_browser
    return HostBrowser.for_sandbox(config.sandbox_name, settings.port, settings.headless)


def validate_host_browser(config: AgyConfig) -> None:
    if not config.sbx.enabled:
        raise HostBrowserError("--host-browser requires Docker Sandboxes (set sbx.enabled: true in agy.yaml).")
    if KIT_CHROME_DEVTOOLS not in config.sbx.kits:
        raise HostBrowserError(
            f"--host-browser needs the '{KIT_CHROME_DEVTOOLS}' kit, which registers the browser MCP server. "
            f"Run: agy-sandbox kits add {KIT_CHROME_DEVTOOLS}"
        )


def host_browser_kit_args(config: AgyConfig) -> List[str]:
    return [f"{KIT_CHROME_HOST}.host_port={config.sbx.host_browser.port}"]


def check_existing_sandbox_host_browser(config: AgyConfig) -> None:
    """Kits are fixed when a sandbox is created (sbx cannot add a kit with startup commands later)."""
    has_host_kit = sandbox_has_env(config.sandbox_name, HOST_BROWSER_ENV_MARKER)
    if config.sbx.host_browser.enabled and not has_host_kit:
        raise HostBrowserError(
            "This sandbox was created without host browser support. "
            "Recreate it with: agy-sandbox up --host-browser --rebuild"
        )
    if not config.sbx.host_browser.enabled and has_host_kit:
        print(
            "⚠️  This sandbox was created with --host-browser, so browser tools have no browser without it. "
            "Run 'agy-sandbox up --host-browser', or 'agy-sandbox up --rebuild' to switch back."
        )


def run_up(config: AgyConfig, rebuild: bool = False) -> None:
    use_host_browser = config.sbx.host_browser.enabled
    if use_host_browser:
        validate_host_browser(config)

    if not config.sbx.enabled:
        image_name = build_project_image(config)
        run_up_docker(config, image_name)
        return

    SbxRunner.check_sbx_availability(config.agent)
    if sandbox_exists(config.sandbox_name) and not rebuild:
        if KIT_CHROME_DEVTOOLS in config.sbx.kits:
            check_existing_sandbox_host_browser(config)
        if use_host_browser:
            host_browser_for(config).start()
        run_up_sbx(config, image_name="", rebuild=False)
        return

    image_name = build_project_image(config)
    extra_kits: List[str] = []
    kit_args: List[str] = []
    if use_host_browser:
        extra_kits, kit_args = [KIT_CHROME_HOST], host_browser_kit_args(config)
        host_browser_for(config).start()
    run_up_sbx(config, image_name, rebuild=rebuild, extra_kits=extra_kits, kit_args=kit_args)


def run_down_sbx(config: AgyConfig) -> None:
    SbxRunner.stop_sandbox(config)
    host_browser_for(config).stop()


def run_down_docker(config: AgyConfig) -> None:
    DockerRunner.stop_container(config)


def run_down(config: AgyConfig) -> None:
    if config.sbx.enabled:
        run_down_sbx(config)
    else:
        run_down_docker(config)


__all__ = [
    "build_base_image",
    "build_omp_base_image",
    "build_prime_base_image",
    "build_project_image",
    "check_existing_sandbox_host_browser",
    "check_for_unsaved_sandbox_work",
    "host_browser_for",
    "host_browser_kit_args",
    "remove_sandbox",
    "run_down",
    "run_down_docker",
    "run_down_sbx",
    "run_up",
    "run_up_docker",
    "run_up_sbx",
    "sandbox_exists",
    "validate_host_browser",
]
