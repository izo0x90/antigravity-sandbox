import os
import subprocess
import tempfile
from pathlib import Path
from .config import AgyConfig

def get_base_dir() -> str:
    # Returns the root directory of the antigravity-sandbox project, where docker/Dockerfile.base lives
    # Assuming this file is in src/agy_sandbox/engine.py
    current_file = Path(__file__).resolve()
    return str(current_file.parent.parent.parent)

def build_base_image() -> None:
    base_dir = get_base_dir()
    dockerfile_path = os.path.join(base_dir, "docker", "Dockerfile.base")
    print("Building agy-base:latest...")
    
    cmd = [
        "docker", "build",
        "-t", "agy-base:latest",
        "-f", dockerfile_path,
        base_dir
    ]
    subprocess.run(cmd, check=True)

def generate_dockerfile(config: AgyConfig) -> str:
    lines = ["FROM agy-base:latest"]
    
    # Optional apt packages
    if config.runtime.apt_packages:
        packages = " ".join(config.runtime.apt_packages)
        lines.append(f"RUN apt-get update && apt-get install -y {packages} && rm -rf /var/lib/apt/lists/*")

    # Install Python via uv
    if config.runtime.python:
        version = config.runtime.python
        lines.append(f"RUN uv python install {version}")
        # Make the uv python the default python on PATH
        lines.append(f"ENV PATH=\"/root/.local/share/uv/python/cpython-{version}-linux-x86_64-gnu/bin:$PATH\"")

    # Install Node via NodeSource
    if config.runtime.node:
        version = config.runtime.node
        lines.append(f"RUN curl -fsSL https://deb.nodesource.com/setup_{version}.x | bash - && apt-get install -y nodejs")

    # Setup scripts
    for script in config.setup_scripts:
        lines.append(f"RUN {script}")
        
    return "\n".join(lines)

def run_up(config: AgyConfig) -> None:
    dockerfile_content = generate_dockerfile(config)
    image_name = f"agy-sandbox-{config.project_name}"
    
    # Create temp dockerfile to build from
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write(dockerfile_content)
        temp_dockerfile_path = f.name
        
    try:
        print(f"Building project image: {image_name}...")
        build_cmd = [
            "docker", "build",
            "-t", image_name,
            "-f", temp_dockerfile_path,
            "."
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
    
    print(f"Starting sandbox container for project {config.project_name}...")
    run_cmd = [
        "docker", "run", "-it", "--rm",
        "--name", f"agy-sandbox-container-{config.project_name}",
        "-v", f"{host_cwd}:{workspace_path}",
        "-v", f"{profile_dir}:/root/.gemini",
        "-w", workspace_path
    ]
    
    for env_var in config.env:
        run_cmd.extend(["-e", env_var])
        
    run_cmd.append(image_name)
    run_cmd.append("/bin/bash")
    
    subprocess.run(run_cmd)

def run_down(config: AgyConfig) -> None:
    container_name = f"agy-sandbox-container-{config.project_name}"
    print(f"Stopping container {container_name} (if running)...")
    
    # We ignore errors here in case it's already stopped
    subprocess.run(["docker", "stop", container_name], capture_output=True)
    subprocess.run(["docker", "rm", container_name], capture_output=True)
