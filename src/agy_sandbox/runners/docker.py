import os
import subprocess
from pathlib import Path
from typing import Tuple

from ..config import AgyConfig
from ..constants import (
    BASE_DOCKERFILE_NAME,
    CONTAINER_GEMINI_CONFIG,
    CONTAINER_GEMINI_HOME,
    CONTAINER_KEYRINGS,
    DEFAULT_BASE_IMAGE,
    DEFAULT_COLORTERM,
    DEFAULT_DOCKERFILE_NAME,
    DEFAULT_TERM,
    LOCAL_DOCKERFILE_NAME,
    OMP_BASE_IMAGE,
    OMP_DOCKERFILE_NAME,
    PRIME_BASE_IMAGE,
    PRIME_DOCKERFILE_NAME,
    SUPPORTED_TERMINALS,
)


class DockerRunner:
    """
    Encapsulates all Docker Engine commands (building base & project images, running/stopping containers).
    """

    @staticmethod
    def resolve_docker_context(dockerfile_name: str) -> Tuple[Path, Path]:
        package_dir = Path(__file__).resolve().parent.parent
        dockerfile_path = package_dir / "docker" / dockerfile_name

        if not dockerfile_path.exists():
            repo_dir = package_dir.parent.parent
            dockerfile_path = repo_dir / "docker" / dockerfile_name
            context_dir = repo_dir
        else:
            context_dir = package_dir / "docker"

        return dockerfile_path, context_dir

    @classmethod
    def build_base_image(cls) -> None:
        dockerfile_path, context_dir = cls.resolve_docker_context(BASE_DOCKERFILE_NAME)

        print(f"Building {DEFAULT_BASE_IMAGE}...")
        cmd = [
            "docker",
            "build",
            "-t",
            DEFAULT_BASE_IMAGE,
            "-f",
            str(dockerfile_path),
            str(context_dir),
        ]
        subprocess.run(cmd, check=True)

    @classmethod
    def ensure_default_base_image(cls) -> None:
        try:
            subprocess.run(["docker", "inspect", DEFAULT_BASE_IMAGE], capture_output=True, check=True)
        except subprocess.SubprocessError:
            cls.build_base_image()

    @classmethod
    def build_stacked_base_image(cls, config: AgyConfig) -> str:
        """
        Sequentially builds and stacks base image layers for all requested agents/kits.
        Returns the final effective base image tag to be passed to the project Dockerfile.
        """
        layer_specs = [
            spec for spec in config.get_requested_agent_specs()
            if spec.dockerfile_name and spec.base_image_tag
        ]

        if not layer_specs:
            return DEFAULT_BASE_IMAGE

        cls.ensure_default_base_image()
        current_base = DEFAULT_BASE_IMAGE

        for spec in layer_specs:
            dockerfile_path, context_dir = cls.resolve_docker_context(spec.dockerfile_name)
            print(f"Building stacked base image layer '{spec.base_image_tag}' from '{current_base}'...")
            cmd = [
                "docker",
                "build",
                "-t",
                spec.base_image_tag,
                "-f",
                str(dockerfile_path),
                "--build-arg",
                f"BASE_IMAGE={current_base}",
                str(context_dir),
            ]
            subprocess.run(cmd, check=True)
            current_base = spec.base_image_tag

        return current_base

    @classmethod
    def build_omp_base_image(cls) -> None:
        """Deprecated wrapper retained for backward compatibility."""
        cls.ensure_default_base_image()
        dockerfile_path, context_dir = cls.resolve_docker_context(OMP_DOCKERFILE_NAME)

        print(f"Building stacked base image {OMP_BASE_IMAGE}...")
        cmd = [
            "docker",
            "build",
            "-t",
            OMP_BASE_IMAGE,
            "-f",
            str(dockerfile_path),
            "--build-arg",
            f"BASE_IMAGE={DEFAULT_BASE_IMAGE}",
            str(context_dir),
        ]
        subprocess.run(cmd, check=True)

    @classmethod
    def build_prime_base_image(cls) -> None:
        """Deprecated wrapper retained for backward compatibility."""
        cls.ensure_default_base_image()
        dockerfile_path, context_dir = cls.resolve_docker_context(PRIME_DOCKERFILE_NAME)

        print(f"Building stacked base image {PRIME_BASE_IMAGE}...")
        cmd = [
            "docker",
            "build",
            "-t",
            PRIME_BASE_IMAGE,
            "-f",
            str(dockerfile_path),
            "--build-arg",
            f"BASE_IMAGE={DEFAULT_BASE_IMAGE}",
            str(context_dir),
        ]
        subprocess.run(cmd, check=True)

    @classmethod
    def build_project_image(cls, config: AgyConfig) -> str:
        image_name = config.image_name
        local_dockerfile = Path(LOCAL_DOCKERFILE_NAME)

        effective_base = cls.build_stacked_base_image(config)

        if local_dockerfile.exists():
            dockerfile_path = local_dockerfile
            print(f"Building project image '{image_name}' using custom project {LOCAL_DOCKERFILE_NAME}...")
        else:
            package_dir = Path(__file__).resolve().parent.parent
            dockerfile_path = package_dir / "docker" / DEFAULT_DOCKERFILE_NAME
            print(f"Building project image '{image_name}' using default package Dockerfile...")

        build_cmd = [
            "docker",
            "build",
            "-t",
            image_name,
            "-f",
            str(dockerfile_path),
        ]

        if "BASE_IMAGE" not in config.build_args:
            build_cmd.extend(["--build-arg", f"BASE_IMAGE={effective_base}"])

        for key, val in config.build_args.items():
            build_cmd.extend(["--build-arg", f"{key}={val}"])

        build_cmd.append(".")

        print(f"Executing: {' '.join(build_cmd)}")
        subprocess.run(build_cmd, check=True)
        return image_name

    @classmethod
    def run_container(cls, config: AgyConfig, image_name: str) -> None:
        host_cwd = os.getcwd()
        workspace_path = config.workspace_path or host_cwd

        profile_prefix = config.agent_spec.profile_dir_name
        if config.use_native_login:
            profile_dir = os.path.expanduser(f"~/{profile_prefix}")
        else:
            profile_dir = os.path.expanduser(f"~/{profile_prefix}_{config.profile}")

        os.makedirs(profile_dir, exist_ok=True)

        term_env = os.environ.get("TERM", DEFAULT_TERM)
        if not term_env or term_env not in SUPPORTED_TERMINALS:
            term_env = DEFAULT_TERM
        colorterm_env = os.environ.get("COLORTERM") or DEFAULT_COLORTERM

        print(f"Starting sandbox container for project {config.project_name}...")
        run_cmd = [
            "docker",
            "run",
            "-it",
            "--rm",
            "--name",
            config.container_name,
            "-e",
            f"TERM={term_env}",
            "-e",
            f"COLORTERM={colorterm_env}",
            "-v",
            f"{host_cwd}:{workspace_path}",
            "-v",
            f"{profile_dir}:{CONTAINER_GEMINI_HOME}",
            "-v",
            f"{profile_dir}:{CONTAINER_GEMINI_CONFIG}",
            "-v",
            f"{profile_dir}/keyrings:{CONTAINER_KEYRINGS}",
            "-w",
            workspace_path,
        ]

        for env_var in config.env:
            run_cmd.extend(["-e", env_var])

        setup_commands = (
            " && ".join(config.setup_scripts) if config.setup_scripts else "true"
        )

        startup_script = (
            "echo 'force_color_prompt=yes' >> /root/.bashrc && "
            "mkdir -p /root/.local/share/keyrings && "
            "if [ ! -f /root/.local/share/keyrings/default ]; then "
            "echo 'login' > /root/.local/share/keyrings/default && "
            "printf '[keyring]\\ndisplay-name=login\\nctime=0\\nmtime=0\\nlock-on-idle=false\\nlock-after=false\\n' > /root/.local/share/keyrings/login.keyring; "
            "fi && "
            "eval $(echo 'agy' | gnome-keyring-daemon --unlock --components=secrets) && "
            f"{setup_commands} && "
            "exec bash"
        )

        run_cmd.extend([image_name, "dbus-run-session", "--", "bash", "-c", startup_script])
        subprocess.run(run_cmd)

    @classmethod
    def stop_container(cls, config: AgyConfig) -> None:
        print(f"Stopping container {config.container_name} (if running)...")
        subprocess.run(["docker", "stop", config.container_name], capture_output=True)
        subprocess.run(["docker", "rm", config.container_name], capture_output=True)
