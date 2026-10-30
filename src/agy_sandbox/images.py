"""
The Docker image pipeline. Images are the only thing Docker is used for: the
project image is built here, then loaded into sbx as a template.

    Dockerfile.base  ->  Dockerfile.<agent> (one layer per requested agent)
                     ->  Dockerfile.agy in the project, or Dockerfile.default
"""
import subprocess
from pathlib import Path
from typing import List, Tuple

from .constants import (
    BASE_DOCKERFILE_NAME,
    DEFAULT_BASE_IMAGE,
    DEFAULT_CMD_TIMEOUT,
    DEFAULT_DOCKERFILE_NAME,
    LOCAL_DOCKERFILE_NAME,
)

DOCKER_DIR = Path(__file__).resolve().parent / "docker"


def dockerfile_path(name: str) -> Path:
    return DOCKER_DIR / name


def _build(tag: str, dockerfile: Path, context: Path, build_args: List[str] = (), no_cache: bool = False) -> None:
    cmd = ["docker", "build", "-t", tag, "-f", str(dockerfile)]
    if no_cache:
        cmd.append("--no-cache")
    for arg in build_args:
        cmd.extend(["--build-arg", arg])
    cmd.append(str(context))
    print(f"Executing: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def image_exists(tag: str) -> bool:
    try:
        res = subprocess.run(["docker", "image", "inspect", tag], capture_output=True, timeout=DEFAULT_CMD_TIMEOUT)
        return res.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def image_id(tag: str) -> str:
    """The full sha256 image ID of a local image, or '' when it does not exist."""
    try:
        res = subprocess.run(
            ["docker", "image", "inspect", "--format={{.Id}}", tag],
            capture_output=True, text=True, timeout=DEFAULT_CMD_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def build_base_image(no_cache: bool = False) -> None:
    print(f"Building {DEFAULT_BASE_IMAGE}...")
    _build(DEFAULT_BASE_IMAGE, dockerfile_path(BASE_DOCKERFILE_NAME), DOCKER_DIR, no_cache=no_cache)


def ensure_base_image() -> None:
    if not image_exists(DEFAULT_BASE_IMAGE):
        build_base_image()


def build_agent_layers(config, no_cache: bool = False) -> str:
    """Stacks one image layer per requested agent on the base image; returns the final tag."""
    ensure_base_image()
    current = DEFAULT_BASE_IMAGE
    for spec in config.requested_agent_specs():
        if not spec.dockerfile:
            continue
        print(f"Building agent layer '{spec.image_tag}' from '{current}'...")
        _build(spec.image_tag, dockerfile_path(spec.dockerfile), DOCKER_DIR, [f"BASE_IMAGE={current}"], no_cache)
        current = spec.image_tag
    return current


def project_dockerfile() -> Tuple[Path, bool]:
    """The project's own Dockerfile.agy when present, else the packaged default."""
    local = Path(LOCAL_DOCKERFILE_NAME)
    if local.exists():
        return local, True
    return dockerfile_path(DEFAULT_DOCKERFILE_NAME), False


def build_project_image(config) -> str:
    base = build_agent_layers(config)
    dockerfile, custom = project_dockerfile()
    source = f"project {LOCAL_DOCKERFILE_NAME}" if custom else "default package Dockerfile"
    print(f"Building project image '{config.image_name}' using {source}...")
    build_args = []
    if "BASE_IMAGE" not in config.build_args:
        build_args.append(f"BASE_IMAGE={base}")
    build_args += [f"{k}={v}" for k, v in config.build_args.items()]
    _build(config.image_name, dockerfile, Path("."), build_args)
    return config.image_name
