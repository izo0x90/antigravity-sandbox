import os
import subprocess
import sys
import tempfile

from ..config import AgyConfig
from ..kits import validate_and_resolve_kits


def sandbox_exists(sandbox_name: str) -> bool:
    try:
        ls_output = subprocess.run(["sbx", "ls"], capture_output=True, text=True, check=True).stdout
        for line in ls_output.strip().splitlines()[1:]:
            if line.strip() and line.split()[0] == sandbox_name:
                return True
    except (subprocess.SubprocessError, FileNotFoundError, IndexError):
        pass
    return False


def get_local_image_id(image_name: str) -> str:
    try:
        cmd = ["docker", "inspect", "--format={{.Id}}", f"{image_name}:latest"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.SubprocessError:
        return ""


def is_image_loaded_in_sbx(image_id: str) -> bool:
    if not image_id:
        return False
    clean_id = image_id.replace("sha256:", "")[:12]
    try:
        result = subprocess.run(["sbx", "template", "ls"], capture_output=True, text=True, check=True)
        return clean_id in result.stdout
    except subprocess.SubprocessError:
        return False


def remove_sandbox(sandbox_name: str) -> None:
    if not sandbox_exists(sandbox_name):
        return

    print(f"Stopping old sandbox '{sandbox_name}'...")
    subprocess.run(["sbx", "stop", sandbox_name], capture_output=True)

    print(f"Removing old sandbox '{sandbox_name}'...")
    res = subprocess.run(["sbx", "rm", "-f", sandbox_name], capture_output=True, text=True)
    if res.returncode != 0:
        res = subprocess.run(["sbx", "rm", sandbox_name], capture_output=True, text=True)

    if sandbox_exists(sandbox_name):
        print(f"\n❌ Error: Failed to remove existing sandbox '{sandbox_name}'.", file=sys.stderr)
        if res.stderr:
            print(f"Details from sbx: {res.stderr.strip()}", file=sys.stderr)
        print("Please stop/remove it manually using 'sbx rm <name>' before retrying.", file=sys.stderr)
        sys.exit(1)


class SbxRunner:
    """
    Encapsulates all Docker Sandboxes daemon operations (running, stopping, removing, template management).
    """

    @classmethod
    def load_template_image(cls, image_name: str) -> None:
        print(f"Loading host image '{image_name}:latest' into Docker Sandboxes template store...")
        fd, temp_tar = tempfile.mkstemp(suffix=".tar")
        os.close(fd)
        try:
            print("Exporting local image to tarball...")
            subprocess.run(["docker", "save", "-o", temp_tar, f"{image_name}:latest"], check=True)
            print("Loading tarball into Docker Sandboxes daemon...")
            subprocess.run(["sbx", "template", "load", temp_tar], check=True)
        except subprocess.SubprocessError as e:
            print(f"Error transferring template image to Docker Sandboxes: {e}", file=sys.stderr)
            print("Please ensure Docker Sandboxes daemon is running.", file=sys.stderr)
            try:
                os.unlink(temp_tar)
            except OSError:
                pass
            sys.exit(1)
        finally:
            try:
                os.unlink(temp_tar)
            except OSError:
                pass

    @classmethod
    def run_sandbox(cls, config: AgyConfig, image_name: str, rebuild: bool = False) -> None:
        sandbox_name = config.sandbox_name
        exists = sandbox_exists(sandbox_name)

        if rebuild and exists:
            remove_sandbox(sandbox_name)
            exists = False

        if exists and not rebuild:
            print(f"Resuming existing sandbox container '{sandbox_name}'...")
            print("💡 Note: To rebuild with updated Docker image or kits, run with '--rebuild'.")
            run_cmd = ["sbx", "run", "--name", sandbox_name, config.sbx.agent]
            print(f"Executing: {' '.join(run_cmd)}")
            res = subprocess.run(run_cmd)
            if res.returncode != 0:
                sys.exit(res.returncode)
            return

        local_id = get_local_image_id(image_name)
        loaded_in_sbx = is_image_loaded_in_sbx(local_id)

        if rebuild or not loaded_in_sbx:
            cls.load_template_image(image_name)
        else:
            print(f"Template image '{image_name}:latest' is already cached in Docker Sandboxes.")

        print(f"Creating and starting new sandbox container '{sandbox_name}'...")
        run_cmd = [
            "sbx",
            "run",
            "--name",
            sandbox_name,
            "--template",
            f"{image_name}:latest",
        ]

        if config.sbx.kits:
            for kit_path in validate_and_resolve_kits(config.sbx.kits):
                run_cmd.extend(["--kit", kit_path])

        if config.sbx.clone:
            run_cmd.append("--clone")

        run_cmd.append(config.sbx.agent)

        print(f"Executing: {' '.join(run_cmd)}")
        res = subprocess.run(run_cmd)
        if res.returncode != 0:
            sys.exit(res.returncode)

    @classmethod
    def stop_sandbox(cls, config: AgyConfig) -> None:
        sandbox_name = config.sandbox_name
        if not sandbox_exists(sandbox_name):
            print(f"Sandbox '{sandbox_name}' is not currently running or active.")
            return

        remove_sandbox(sandbox_name)
        print("Done.")
