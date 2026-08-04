from .config import AgyConfig
from .git_guardian import check_for_unsaved_sandbox_work
from .runners.docker import DockerRunner
from .runners.sbx import SbxRunner, remove_sandbox, sandbox_exists


def build_base_image() -> None:
    DockerRunner.build_base_image()


def build_omp_base_image() -> None:
    DockerRunner.build_omp_base_image()


def build_project_image(config: AgyConfig) -> str:
    return DockerRunner.build_project_image(config)


def run_up_sbx(config: AgyConfig, image_name: str, rebuild: bool = False) -> None:
    SbxRunner.run_sandbox(config, image_name, rebuild=rebuild)


def run_up_docker(config: AgyConfig, image_name: str) -> None:
    DockerRunner.run_container(config, image_name)


def run_up(config: AgyConfig, rebuild: bool = False) -> None:
    image_name = build_project_image(config)
    if config.sbx.enabled:
        run_up_sbx(config, image_name, rebuild=rebuild)
    else:
        run_up_docker(config, image_name)


def run_down_sbx(config: AgyConfig) -> None:
    SbxRunner.stop_sandbox(config)


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
    "build_project_image",
    "check_for_unsaved_sandbox_work",
    "remove_sandbox",
    "run_down",
    "run_down_docker",
    "run_down_sbx",
    "run_up",
    "run_up_docker",
    "run_up_sbx",
    "sandbox_exists",
]
