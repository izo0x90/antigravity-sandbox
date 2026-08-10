import os
import shutil
import subprocess
import sys
import tempfile

from ..config import AgyConfig
from ..constants import (
    DAEMON_PREFLIGHT_TIMEOUT,
    DEFAULT_CMD_TIMEOUT,
    REMOVE_CMD_TIMEOUT,
    SHORT_IMAGE_ID_LEN,
)
from ..kits import validate_and_resolve_kits


def sandbox_exists(sandbox_name: str) -> bool:
    try:
        ls_output = subprocess.run(["sbx", "ls"], capture_output=True, text=True, check=True, timeout=DEFAULT_CMD_TIMEOUT).stdout
        for line in ls_output.strip().splitlines()[1:]:
            if line.strip() and line.split()[0] == sandbox_name:
                return True
    except (subprocess.SubprocessError, FileNotFoundError, IndexError, TimeoutError):
        pass
    return False


def get_local_image_id(image_name: str) -> str:
    try:
        cmd = ["docker", "inspect", "--format={{.Id}}", f"{image_name}:latest"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=DEFAULT_CMD_TIMEOUT)
        return result.stdout.strip()
    except (subprocess.SubprocessError, TimeoutError):
        return ""


def is_image_loaded_in_sbx(image_id: str) -> bool:
    if not image_id:
        return False
    clean_id = image_id.replace("sha256:", "")[:SHORT_IMAGE_ID_LEN]
    try:
        result = subprocess.run(["sbx", "template", "ls"], capture_output=True, text=True, check=True, timeout=DEFAULT_CMD_TIMEOUT)
        return clean_id in result.stdout
    except (subprocess.SubprocessError, TimeoutError):
        return False


def remove_sandbox(sandbox_name: str) -> None:
    if not sandbox_exists(sandbox_name):
        return

    print(f"Stopping old sandbox '{sandbox_name}'...")
    subprocess.run(["sbx", "stop", sandbox_name], capture_output=True, timeout=REMOVE_CMD_TIMEOUT)

    print(f"Removing old sandbox '{sandbox_name}'...")
    res = subprocess.run(["sbx", "rm", "-f", sandbox_name], capture_output=True, text=True, timeout=REMOVE_CMD_TIMEOUT)
    if res.returncode != 0:
        res = subprocess.run(["sbx", "rm", sandbox_name], capture_output=True, text=True, timeout=REMOVE_CMD_TIMEOUT)

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
    def check_sbx_availability(cls, required_agent: str = "agy") -> None:
        """
        Preflight check executed BEFORE any Docker image building or tarball exports.
        Verifies that the sbx CLI binary is available on PATH and sandboxd is responding.
        """
        if not shutil.which("sbx"):
            print(
                f"❌ Error: Docker Sandboxes CLI ('sbx') is not installed or not on PATH.\n"
                f"Agent harness '{required_agent}' requires Docker Sandboxes.\n"
                f"Please install 'sbx' or Docker Desktop with Sandboxes support.",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            res = subprocess.run(["sbx", "ls"], capture_output=True, text=True, timeout=DAEMON_PREFLIGHT_TIMEOUT)
            if res.returncode != 0:
                print(
                    f"❌ Error: Docker Sandboxes daemon ('sandboxd') is unreachable.\n"
                    f"Details from sbx: {res.stderr.strip()}\n"
                    f"Please ensure Docker Sandboxes is running.",
                    file=sys.stderr,
                )
                sys.exit(1)
        except (subprocess.SubprocessError, TimeoutError, OSError) as e:
            print(
                f"❌ Error: Failed to communicate with Docker Sandboxes daemon: {e}\n"
                f"Please ensure 'sandboxd' is running.",
                file=sys.stderr,
            )
            sys.exit(1)

    @classmethod
    def ensure_secret_configured(cls, config: AgyConfig) -> None:
        if config.auth_mode != "sbx_proxy":
            return

        secret_services = set()
        for spec in config.get_requested_agent_specs():
            if spec.sbx_secret_services:
                secret_services.update(spec.sbx_secret_services)

        if not secret_services:
            return

        sandbox_name = config.sandbox_name
        for service in sorted(secret_services):
            print(f"Configuring SBX proxy secret for service '{service}' in sandbox '{sandbox_name}'...")
            cmd = ["sbx", "secret", "set", sandbox_name, service]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    errMsg = res.stderr.strip() if res.stderr else f"exit code {res.returncode}"
                    print(f"⚠️ Warning: Could not configure SBX proxy secret for service '{service}': {errMsg}")
            except subprocess.SubprocessError as e:
                print(f"⚠️ Warning: Could not configure SBX proxy secret for service '{service}': {e}")

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
        cls.check_sbx_availability(config.agent)
        cls.ensure_secret_configured(config)

        sandbox_name = config.sandbox_name
        exists = sandbox_exists(sandbox_name)
        sbx_agent_arg = config.agent_spec.sbx_agent_arg

        if rebuild and exists:
            remove_sandbox(sandbox_name)
            exists = False

        if exists and not rebuild:
            print(f"Resuming existing sandbox container '{sandbox_name}'...")
            print("💡 Note: To rebuild with updated Docker image or kits, run with '--rebuild'.")
            run_cmd = ["sbx", "run", "--name", sandbox_name]
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

        run_cmd.append(sbx_agent_arg)

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
