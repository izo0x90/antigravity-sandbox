import os
import subprocess
import tempfile
from pathlib import Path
from .config import AgyConfig


def build_base_image() -> None:
    # Look for the docker folder inside the agy_sandbox package directory
    package_dir = Path(__file__).resolve().parent
    dockerfile_path = package_dir / "docker" / "Dockerfile.base"

    # Fallback to dev repository structure if not found inside package
    if not dockerfile_path.exists():
        repo_dir = package_dir.parent.parent
        dockerfile_path = repo_dir / "docker" / "Dockerfile.base"
        context_dir = repo_dir
    else:
        context_dir = package_dir / "docker"

    print("Building agy-base:latest...")
    cmd = [
        "docker",
        "build",
        "-t",
        "agy-base:latest",
        "-f",
        str(dockerfile_path),
        str(context_dir),
    ]
    subprocess.run(cmd, check=True)


def generate_dockerfile(config: AgyConfig) -> str:
    lines = ["FROM agy-base:latest"]

    # Optional apt packages
    if config.runtime.apt_packages:
        packages = " ".join(config.runtime.apt_packages)
        lines.append(
            f"RUN apt-get update && apt-get install -y {packages} && rm -rf /var/lib/apt/lists/*"
        )

    # Install Python via uv
    if config.runtime.python:
        version = config.runtime.python
        lines.append(f"RUN uv python install {version}")
        # Make the uv python the default python on PATH
        lines.append(
            f'ENV PATH="/root/.local/share/uv/python/cpython-{version}-linux-x86_64-gnu/bin:$PATH"'
        )

    # Install Node via NodeSource
    if config.runtime.node:
        version = config.runtime.node
        lines.append(
            f"RUN curl -fsSL https://deb.nodesource.com/setup_{version}.x | bash - && apt-get install -y nodejs"
        )

    return "\n".join(lines)


def run_up(config: AgyConfig) -> None:
    dockerfile_content = generate_dockerfile(config)
    image_name = f"agy-sandbox-{config.project_name}"

    # Create temp dockerfile to build from
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(dockerfile_content)
        temp_dockerfile_path = f.name

    try:
        print(f"Building project image: {image_name}...")
        build_cmd = [
            "docker",
            "build",
            "-t",
            image_name,
            "-f",
            temp_dockerfile_path,
            ".",
        ]
        subprocess.run(build_cmd, check=True)
    finally:
        os.unlink(temp_dockerfile_path)

    # Calculate mounts
    host_cwd = os.getcwd()
    workspace_path = config.workspace_path or host_cwd

    if config.use_native_login:
        profile_dir = os.path.expanduser("~/.gemini")
    else:
        profile_dir = os.path.expanduser(f"~/.gemini_{config.profile}")

    os.makedirs(profile_dir, exist_ok=True)

    # Force-upgrade low-color or empty terminal environments to 256color & truecolor
    term_env = os.environ.get("TERM", "xterm-256color")
    if not term_env or term_env == "xterm":
        term_env = "xterm-256color"
    colorterm_env = os.environ.get("COLORTERM") or "truecolor"

    print(f"Starting sandbox container for project {config.project_name}...")
    run_cmd = [
        "docker",
        "run",
        "-it",
        "--rm",
        "--name",
        f"agy-sandbox-container-{config.project_name}",
        "-e",
        f"TERM={term_env}",
        "-e",
        f"COLORTERM={colorterm_env}",
        "-v",
        f"{host_cwd}:{workspace_path}",
        "-v",
        f"{profile_dir}:/root/.gemini",
        "-v",
        f"{profile_dir}:/root/.config/gemini",
        "-v",
        f"{profile_dir}/keyrings:/root/.local/share/keyrings",
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


def run_down(config: AgyConfig) -> None:
    container_name = f"agy-sandbox-container-{config.project_name}"
    print(f"Stopping container {container_name} (if running)...")

    # We ignore errors here in case it's already stopped
    subprocess.run(["docker", "stop", container_name], capture_output=True)
    subprocess.run(["docker", "rm", container_name], capture_output=True)
