import json
import subprocess
import unittest
from unittest.mock import patch

from agy_sandbox import sandbox
from agy_sandbox.sandbox import SandboxError

LS = json.dumps({"sandboxes": [{"name": "agy-sandbox-demo", "agent": "shell", "status": "stopped"}]})
INSPECT = json.dumps({"name": "agy-sandbox-demo", "kits": ["/pkg/kits/claude", "/pkg/kits/chrome-host/"]})
TEMPLATES = json.dumps({"images": [{"id": "abcdef123456", "repository": "docker.io/library/x", "tag": "latest"}]})
SECRETS = json.dumps({"secrets": [
    {"scope": "global", "type": "service", "name": "anthropic"},
    {"scope": "agy-sandbox-demo", "type": "service", "name": "google"},
    {"scope": "agy-sandbox-other", "type": "service", "name": "openai"},
    {"scope": "global", "type": "registry", "name": "ghcr.io"},
]})


def completed(stdout="", code=0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr="")


class TestSbxWrapper(unittest.TestCase):
    def test_calls_never_read_stdin(self):
        with patch("subprocess.run", return_value=completed(LS)) as run:
            sandbox.list_sandboxes()
        self.assertEqual(run.call_args[0][0], ["sbx", "ls", "--json"])
        self.assertIs(run.call_args[1]["stdin"], subprocess.DEVNULL)

    def test_sandbox_exists(self):
        with patch("subprocess.run", return_value=completed(LS)):
            self.assertTrue(sandbox.sandbox_exists("agy-sandbox-demo"))
            self.assertFalse(sandbox.sandbox_exists("agy-sandbox-nope"))

    def test_kit_names_from_inspect(self):
        with patch("subprocess.run", return_value=completed(INSPECT)):
            self.assertEqual(sandbox.sandbox_kit_names("agy-sandbox-demo"), ["claude", "chrome-host"])

    def test_template_loaded_matches_short_id(self):
        with patch("subprocess.run", return_value=completed(TEMPLATES)):
            self.assertTrue(sandbox.template_loaded("sha256:abcdef123456789"))
            self.assertFalse(sandbox.template_loaded("sha256:000000000000"))
            self.assertFalse(sandbox.template_loaded(""))

    def test_stored_secret_services_scoping(self):
        with patch("subprocess.run", return_value=completed(SECRETS)):
            self.assertEqual(sandbox.stored_secret_services("agy-sandbox-demo"), {"anthropic", "google"})

    def test_create_command(self):
        with patch("subprocess.run", return_value=completed()) as run:
            sandbox.create(
                "agy-sandbox-demo", "img:latest", "/work", kits=["/k/claude", "/k/agy-proxy"],
                kit_args=["claude.auth_mode=proxy-managed"], env=["A=1"], ports=["3000:3000"], clone=True,
            )
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:8], ["sbx", "create", "--name", "agy-sandbox-demo", "--template", "img:latest", "--pull", "never"])
        self.assertEqual(cmd[-2:], ["shell", "/work"])
        self.assertIn("--clone", cmd)
        for pair in (["--kit", "/k/claude"], ["--kit", "/k/agy-proxy"], ["--kit-arg", "claude.auth_mode=proxy-managed"],
                     ["-e", "A=1"], ["--publish", "3000:3000"]):
            idx = cmd.index(pair[1])
            self.assertEqual(cmd[idx - 1], pair[0])
        self.assertIs(run.call_args[1]["stdin"], subprocess.DEVNULL)
        with patch("subprocess.run", return_value=completed(code=1)):
            with self.assertRaises(SandboxError):
                sandbox.create("agy-sandbox-demo", "img:latest", "/work", kits=[])

    def test_snapshot_and_copy_helpers(self):
        with patch("subprocess.run", return_value=completed()) as run, patch("os.makedirs"):
            sandbox.stop_sandbox("s")
            sandbox.save_template("s", "img:upgrade-1")
            sandbox.remove_template("img:upgrade-0")
            sandbox.copy_from_sandbox("s", "/home/agent/.claude/projects", "/staging/0")
            sandbox.copy_to_sandbox("/staging/0/projects", "s", "/home/agent/.claude")
            sandbox.exec_script("s", "chown -R agent:agent /x", user="root")
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds[0], ["sbx", "stop", "s"])
        self.assertEqual(cmds[1], ["sbx", "template", "save", "s", "img:upgrade-1"])
        self.assertEqual(cmds[2], ["sbx", "template", "rm", "-f", "img:upgrade-0"])
        self.assertEqual(cmds[3], ["sbx", "cp", "s:/home/agent/.claude/projects", "/staging/0"])
        self.assertEqual(cmds[4], ["sbx", "cp", "/staging/0/projects", "s:/home/agent/.claude"])
        self.assertEqual(cmds[5], ["sbx", "exec", "-u", "root", "s", "sh", "-c", "chown -R agent:agent /x"])
        with patch("subprocess.run", return_value=completed(code=1)):
            with self.assertRaises(SandboxError):
                sandbox.save_template("s", "x:y")

    def test_attach_passes_env(self):
        with patch("subprocess.run", return_value=completed()) as run:
            sandbox.attach("agy-sandbox-demo", env=["A=1"])
        self.assertEqual(run.call_args[0][0], ["sbx", "run", "--name", "agy-sandbox-demo", "-e", "A=1"])

    def test_publish_ports(self):
        with patch("subprocess.run", return_value=completed()) as run:
            sandbox.publish_ports("agy-sandbox-demo", ["3000:3000", "8080"])
            sandbox.publish_ports("agy-sandbox-demo", [])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[0][0], ["sbx", "ports", "agy-sandbox-demo", "--publish", "3000:3000", "--publish", "8080"])

    def test_remove_sandbox_raises_when_still_present(self):
        with patch("agy_sandbox.sandbox.sandbox_exists", return_value=True), \
             patch("subprocess.run", return_value=completed(code=1)):
            with self.assertRaises(SandboxError):
                sandbox.remove_sandbox("agy-sandbox-demo")

    def test_remove_sandbox_stops_then_removes(self):
        with patch("agy_sandbox.sandbox.sandbox_exists", side_effect=[True, False]), \
             patch("subprocess.run", return_value=completed()) as run:
            sandbox.remove_sandbox("agy-sandbox-demo")
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(cmds, [["sbx", "stop", "agy-sandbox-demo"], ["sbx", "rm", "-f", "agy-sandbox-demo"]])

    def test_require_sbx(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(SandboxError, "not installed"):
                sandbox.require_sbx()
        with patch("shutil.which", return_value="/usr/local/bin/sbx"), \
             patch("subprocess.run", return_value=completed("Status: stopped\n")):
            with self.assertRaisesRegex(SandboxError, "not running"):
                sandbox.require_sbx()
        with patch("shutil.which", return_value="/usr/local/bin/sbx"), \
             patch("subprocess.run", return_value=completed("Status: running\n")) as run:
            sandbox.require_sbx()
        self.assertEqual(run.call_args[0][0], ["sbx", "daemon", "status"])

    def test_missing_binary_and_timeout_become_sandbox_errors(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(SandboxError):
                sandbox.list_sandboxes()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sbx", 10)):
            with self.assertRaises(SandboxError):
                sandbox.list_sandboxes()

    def test_exec_script(self):
        with patch("subprocess.run", return_value=completed("SESSION /x")) as run:
            res = sandbox.exec_script("agy-sandbox-demo", "echo hi")
        self.assertEqual(res.stdout, "SESSION /x")
        self.assertEqual(run.call_args[0][0], ["sbx", "exec", "agy-sandbox-demo", "sh", "-c", "echo hi"])
        self.assertIs(run.call_args[1]["stdin"], subprocess.DEVNULL)

    def test_load_template(self):
        with patch("subprocess.run", return_value=completed()) as run, patch("os.unlink"):
            sandbox.load_template("img")
        cmds = [c[0][0][:3] for c in run.call_args_list]
        self.assertEqual(cmds[0][:2], ["docker", "save"])
        self.assertEqual(cmds[1], ["sbx", "template", "load"])


if __name__ == "__main__":
    unittest.main()
