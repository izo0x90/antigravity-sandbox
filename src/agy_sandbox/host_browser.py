"""
Manages a Chrome instance on the host that agents inside a sandbox drive over the
Chrome DevTools Protocol (CDP). The sandbox side is handled by kits/chrome-host.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import time
from typing import Optional
import urllib.request

from .constants import HOST_BROWSER_STARTUP_TIMEOUT, HOST_BROWSER_STATE_DIR

MACOS_CHROME_BINARIES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
LINUX_CHROME_BINARIES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


class HostBrowserError(RuntimeError):
    """Raised when the host browser cannot be found, started, or configured."""


def find_chrome_binary() -> Optional[str]:
    if platform.system() == "Darwin":
        for path in MACOS_CHROME_BINARIES:
            if os.path.exists(path):
                return path
    for name in LINUX_CHROME_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    return None


@dataclass(frozen=True)
class HostBrowser:
    port: int
    state_dir: Path
    headless: bool = False

    @classmethod
    def for_sandbox(cls, sandbox_name: str, port: int, headless: bool = False) -> "HostBrowser":
        return cls(port=port, state_dir=Path(HOST_BROWSER_STATE_DIR).expanduser() / sandbox_name, headless=headless)

    @property
    def profile_dir(self) -> Path:
        # A dedicated profile is required: Chrome refuses remote debugging on the default profile.
        return self.state_dir / "profile"

    @property
    def pid_file(self) -> Path:
        return self.state_dir / "chrome.pid"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "chrome.log"

    def is_responding(self) -> bool:
        """True if a Chrome DevTools endpoint answers on the port."""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=1) as resp:
                return "Browser" in json.load(resp)
        except (OSError, ValueError):
            return False

    def is_port_in_use(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            return sock.connect_ex(("127.0.0.1", self.port)) == 0

    def start(self) -> None:
        """Starts Chrome detached from this process, or reuses one already answering on the port."""
        if self.is_responding():
            print(f"Reusing host browser already listening on port {self.port}.")
            return
        if self.is_port_in_use():
            raise HostBrowserError(
                f"Port {self.port} is in use by something other than Chrome. "
                "Set sbx.host_browser.port in agy.yaml to a free port."
            )

        binary = find_chrome_binary()
        if not binary:
            raise HostBrowserError("Google Chrome or Chromium was not found on the host; it is required for --host-browser.")

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            binary,
            f"--remote-debugging-port={self.port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if self.headless:
            cmd.append("--headless=new")

        print(f"Starting host browser on port {self.port} (profile: {self.profile_dir})...")
        with open(self.log_file, "ab") as log:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        self.pid_file.write_text(str(proc.pid))

        deadline = time.monotonic() + HOST_BROWSER_STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self.is_responding():
                return
            if proc.poll() is not None:
                raise HostBrowserError(f"Host browser exited during startup (code {proc.returncode}). See {self.log_file}.")
            time.sleep(0.25)
        raise HostBrowserError(
            f"Host browser did not answer on port {self.port} within {HOST_BROWSER_STARTUP_TIMEOUT}s. See {self.log_file}."
        )

    def stop(self) -> None:
        """Stops the browser only if this tool started it; a reused browser is left running."""
        try:
            pid = int(self.pid_file.read_text())
        except (OSError, ValueError):
            return
        self.pid_file.unlink(missing_ok=True)

        # Guard against a stale pid file (e.g. after a reboot) pointing at an unrelated process.
        if not self.is_responding():
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        print(f"Stopped host browser (pid {pid}).")
