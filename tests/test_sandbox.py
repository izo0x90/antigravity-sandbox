import json
import subprocess
import unittest
from unittest.mock import patch

from agy_sandbox import sandbox
from agy_sandbox.compose import SandboxPlan
from agy_sandbox.errors import SandboxError, SandboxUnavailable
from agy_sandbox.kits import KitRef
from agy_sandbox.workload import WorkloadRef

from tests.support import FakeSbx, captured_console, messages_containing

RUN = "agy_sandbox.sandbox.subprocess.run"
WHICH = "agy_sandbox.sandbox.shutil.which"

LS_OBJECT = json.dumps({"sandboxes": [{"name": "agy-sandbox-demo", "status": "stopped"}]})
LS_ARRAY = json.dumps([{"name": "agy-sandbox-demo", "state": "running"}])
SECRETS = json.dumps({"secrets": [
    {"scope": "global", "type": "service", "name": "anthropic"},
    {"scope": "agy-sandbox-demo", "type": "service", "name": "google"},
    {"scope": "agy-sandbox-other", "type": "service", "name": "openai"},
    {"scope": "global", "type": "registry", "name": "ghcr.io"},
]})


def plan(**overrides) -> SandboxPlan:
    base = dict(
        name="agy-sandbox-demo", workspace="/w", clone=False, auth_mode="sandbox-local",
        workload=WorkloadRef(dir="/state/kits/agy-sandbox-demo/workload", fingerprint="sha256:x"),
        mixins=(KitRef("claude", "/pkg/kits/claude", True), KitRef("pw", "docker.io/sbx/playwright:1", False)),
        kit_args=("chrome-host.host_port=9222",), env=("A=1",), ports=("3000:3000",),
    )
    base.update(overrides)
    return SandboxPlan(**base)


class TestPlumbing(unittest.TestCase):
    def test_non_interactive_calls_close_stdin_and_capture_output(self):
        fake = FakeSbx().respond("sbx", "ls", stdout=LS_OBJECT)
        with patch(RUN, fake):
            sandbox.list_sandboxes()
        self.assertEqual(fake.calls, [["sbx", "ls", "--json"]])
        self.assertIs(fake.kwargs[0]["stdin"], subprocess.DEVNULL)
        self.assertIs(fake.kwargs[0]["stderr"], subprocess.PIPE)

    def test_missing_binary_is_unavailable_with_install_hint(self):
        fake = FakeSbx().raise_on("sbx", exc=FileNotFoundError())
        with patch(RUN, fake), self.assertRaises(SandboxUnavailable) as ctx:
            sandbox.list_sandboxes()
        self.assertIn("brew install", ctx.exception.hint)

    def test_not_signed_in_is_unavailable_with_login_hint(self):
        fake = FakeSbx().respond("sbx", "ls", returncode=1, stderr="error: request failed: 401 Unauthorized: ...\n  try: sbx login")
        with patch(RUN, fake), self.assertRaises(SandboxUnavailable) as ctx:
            sandbox.list_sandboxes()
        self.assertEqual(ctx.exception.hint, "Run: sbx login")

    def test_failures_carry_command_exit_code_and_stderr_tail(self):
        fake = FakeSbx().respond("sbx", "stop", returncode=3, stderr="line1\nline2\nfatal: nope")
        with patch(RUN, fake), self.assertRaises(SandboxError) as ctx:
            sandbox.stop("agy-sandbox-demo")
        err = ctx.exception
        self.assertEqual(err.command, ("sbx", "stop", "agy-sandbox-demo"))
        self.assertEqual(err.returncode, 3)
        self.assertIn("fatal: nope", err.hint)
        self.assertIn("exit 3", err.message)

    def test_timeout_is_a_sandbox_error(self):
        fake = FakeSbx().raise_on("sbx", "ls", exc=subprocess.TimeoutExpired(cmd="sbx ls", timeout=10))
        with patch(RUN, fake), self.assertRaises(SandboxError) as ctx:
            sandbox.list_sandboxes()
        self.assertIn("did not finish", ctx.exception.message)

    def test_sign_in_text_before_json_is_shown_and_skipped(self):
        fake = FakeSbx().respond("sbx", "ls", stdout="Signed in as me\n" + LS_ARRAY)
        with patch(RUN, fake), captured_console() as out:
            names = [s["name"] for s in sandbox.list_sandboxes()]
        self.assertEqual(names, ["agy-sandbox-demo"])
        self.assertEqual(messages_containing(out, "Signed in as me"), ["Signed in as me"])

    def test_login_marker_on_a_successful_call_is_not_an_error(self):
        fake = FakeSbx().respond("sbx", "ls", stdout=LS_ARRAY, stderr="tip: run sbx login to refresh")
        with patch(RUN, fake):
            self.assertEqual(len(sandbox.list_sandboxes()), 1)

    def test_brackets_in_the_preamble_do_not_break_parsing(self):
        fake = FakeSbx().respond("sbx", "ls", stdout="[info] using builder [sbx-kit-builder]\n" + LS_ARRAY)
        with patch(RUN, fake), captured_console() as out:
            self.assertEqual(len(sandbox.list_sandboxes()), 1)
        self.assertTrue(messages_containing(out, "using builder"))

    def test_non_json_output_is_a_sandbox_error(self):
        fake = FakeSbx().respond("sbx", "ls", stdout="nothing here")
        with patch(RUN, fake), self.assertRaises(SandboxError):
            sandbox.list_sandboxes()


class TestVersionAndPreflight(unittest.TestCase):
    def test_version_parses_the_cli_banner(self):
        fake = FakeSbx().respond("sbx", "version", stdout="sbx version: v0.50.1 abcdef\n")
        with patch(RUN, fake):
            v = sandbox.version()
        self.assertEqual((v.major, v.minor, v.patch), (0, 50, 1))
        self.assertTrue(v.as_tuple >= (0, 48, 0))
        self.assertEqual(str(v), "0.50.1")

    def test_unparsable_version_is_unavailable(self):
        fake = FakeSbx().respond("sbx", "version", stdout="??")
        with patch(RUN, fake), self.assertRaises(SandboxUnavailable):
            sandbox.version()

    def test_require_checks_binary_version_and_daemon(self):
        fake = FakeSbx().respond("sbx", "version", stdout="sbx version: v0.46.0").respond("sbx", "daemon", "status", stdout="Status: running")
        with patch(WHICH, return_value="/opt/homebrew/bin/sbx"), patch(RUN, fake):
            self.assertEqual(str(sandbox.require()), "0.46.0")
        self.assertEqual(fake.verbs(), ["version", "daemon"])

    def test_require_without_binary_or_daemon(self):
        with patch(WHICH, return_value=None), self.assertRaises(SandboxUnavailable):
            sandbox.require()
        fake = FakeSbx().respond("sbx", "version", stdout="v0.46.0").respond("sbx", "daemon", "status", stdout="Status: stopped")
        with patch(WHICH, return_value="/bin/sbx"), patch(RUN, fake), self.assertRaises(SandboxUnavailable) as ctx:
            sandbox.require()
        self.assertIn("sbx daemon start", ctx.exception.hint)


class TestSandboxes(unittest.TestCase):
    def test_list_and_exists_accept_both_json_shapes(self):
        for doc in (LS_OBJECT, LS_ARRAY):
            fake = FakeSbx().respond("sbx", "ls", stdout=doc)
            with patch(RUN, fake):
                self.assertTrue(sandbox.exists("agy-sandbox-demo"))
                self.assertFalse(sandbox.exists("agy-sandbox-nope"))

    def test_inspect_maps_old_and_new_field_names(self):
        old = json.dumps({"name": "x", "status": "stopped", "kits": ["/pkg/kits/claude", "/pkg/kits/chrome-host/"],
                          "workspace": "/w", "source_repo_dir": "/src"})
        new = json.dumps({"name": "x", "state": "running", "kit_refs": [{"ref": "/pkg/kits/claude"}],
                          "workspace_path": "/w2", "clone": True})
        with patch(RUN, FakeSbx().respond("sbx", "inspect", stdout=old)):
            info = sandbox.inspect("x")
        self.assertEqual((info.state, info.kits, info.workspace, info.clone, info.running),
                         ("stopped", ("/pkg/kits/claude", "/pkg/kits/chrome-host/"), "/w", True, False))
        with patch(RUN, FakeSbx().respond("sbx", "inspect", stdout=new)):
            info = sandbox.inspect("x")
        self.assertEqual((info.state, info.kits, info.workspace, info.clone, info.running),
                         ("running", ("/pkg/kits/claude",), "/w2", True, True))

    def test_create_argv_is_exact_and_output_passes_through(self):
        fake = FakeSbx()
        with patch(RUN, fake), captured_console():
            sandbox.create(plan(clone=True))
        self.assertEqual(fake.calls, [[
            "sbx", "create", "--name", "agy-sandbox-demo", "--clone", "-e", "A=1", "--publish", "3000:3000",
            "--kit", "/pkg/kits/claude", "--kit", "docker.io/sbx/playwright:1", "--kit-arg", "chrome-host.host_port=9222",
            "/state/kits/agy-sandbox-demo/workload", "/w",
        ]])
        self.assertNotIn("stdin", fake.kwargs[0])   # sbx may prompt to confirm permissions
        self.assertNotIn("stdout", fake.kwargs[0])

    def test_create_failure_is_a_sandbox_error(self):
        fake = FakeSbx().fail("sbx", "create", returncode=2)
        with patch(RUN, fake), captured_console(), self.assertRaises(SandboxError) as ctx:
            sandbox.create(plan())
        self.assertEqual(ctx.exception.returncode, 2)

    def test_attach_is_interactive_and_returns_the_exit_code(self):
        fake = FakeSbx().respond("sbx", "run", returncode=7)
        with patch(RUN, fake), captured_console():
            self.assertEqual(sandbox.attach("agy-sandbox-demo", env=["A=1"]), 7)
        self.assertEqual(fake.calls, [["sbx", "run", "--name", "agy-sandbox-demo", "-e", "A=1"]])
        self.assertNotIn("stdin", fake.kwargs[0])

    def test_kit_add_argv(self):
        fake = FakeSbx()
        with patch(RUN, fake), captured_console():
            sandbox.kit_add("agy-sandbox-demo", KitRef("claude-credentials", "/pkg/kits/claude-credentials", True), ["x.y=1"])
        self.assertEqual(fake.calls, [["sbx", "kit", "add", "agy-sandbox-demo", "/pkg/kits/claude-credentials", "--kit-arg", "x.y=1"]])

    def test_remove_verifies_the_sandbox_is_gone(self):
        fake = FakeSbx().respond("sbx", "ls", stdout=LS_OBJECT).fail("sbx", "rm", stderr="busy")
        with patch(RUN, fake), captured_console(), self.assertRaises(SandboxError) as ctx:
            sandbox.remove("agy-sandbox-demo")
        self.assertEqual(fake.verbs(), ["ls", "stop", "rm", "ls"])
        self.assertIn("sbx rm -f agy-sandbox-demo", ctx.exception.hint)
        self.assertIn("busy", ctx.exception.hint)
        fake = FakeSbx().respond("sbx", "ls", stdout=json.dumps([]))
        with patch(RUN, fake), captured_console():
            sandbox.remove("agy-sandbox-demo")
        self.assertEqual(fake.verbs(), ["ls"])

    def test_publish_ports_warns_instead_of_failing(self):
        fake = FakeSbx().fail("sbx", "port")
        with patch(RUN, fake), captured_console() as out:
            sandbox.publish_ports("agy-sandbox-demo", ["3000:3000", "8080:80"])
        self.assertEqual(fake.calls, [["sbx", "port", "publish", "agy-sandbox-demo", "3000:3000", "8080:80"]])
        self.assertTrue(messages_containing(out, "published ports may be out of date"))
        fake = FakeSbx()
        with patch(RUN, fake):
            sandbox.publish_ports("agy-sandbox-demo", [])
        self.assertEqual(fake.calls, [])

    def test_exec_script_never_raises_on_non_zero(self):
        fake = FakeSbx().respond("sbx", "exec", returncode=1, stdout="SESSION x\n")
        with patch(RUN, fake):
            res = sandbox.exec_script("agy-sandbox-demo", "echo", user="root")
        self.assertEqual(res.returncode, 1)
        self.assertEqual(fake.calls, [["sbx", "exec", "-u", "root", "agy-sandbox-demo", "sh", "-c", "echo"]])


class TestTemplatesAndSecrets(unittest.TestCase):
    def test_templates_both_shapes(self):
        for doc in (json.dumps({"images": [{"repository": "r", "tag": "t"}]}), json.dumps([{"repository": "r", "tag": "t"}])):
            with patch(RUN, FakeSbx().respond("sbx", "template", "ls", stdout=doc)):
                self.assertEqual(sandbox.list_templates(), [{"repository": "r", "tag": "t"}])

    def test_create_from_template_argv_matches_create_plus_template(self):
        fake = FakeSbx()
        with patch(RUN, fake), captured_console():
            sandbox.create_from_template(plan(clone=True), "agy-sandbox-demo:recreate-1")
        self.assertEqual(fake.calls[0][:8], ["sbx", "create", "--name", "agy-sandbox-demo", "--template", "agy-sandbox-demo:recreate-1", "--pull", "never"])
        self.assertEqual(fake.calls[0][8:], ["--clone", "-e", "A=1", "--publish", "3000:3000", "--kit", "/pkg/kits/claude",
                                             "--kit", "docker.io/sbx/playwright:1", "--kit-arg", "chrome-host.host_port=9222",
                                             "/state/kits/agy-sandbox-demo/workload", "/w"])
        self.assertEqual(sandbox.create_from_template_argv(plan(clone=True), "agy-sandbox-demo:recreate-1"), fake.calls[0])

    def test_remove_template_warns_on_failure(self):
        with patch(RUN, FakeSbx().fail("sbx", "template", "rm")), captured_console() as out:
            sandbox.remove_template("x:recreate-1")
        self.assertTrue(messages_containing(out, "sbx template rm -f x:recreate-1"))

    def test_copy_argv(self):
        fake = FakeSbx()
        with patch(RUN, fake), patch("os.makedirs"):
            sandbox.copy_from("s", "/home/agent/.claude/projects", "/tmp/stage/0")
            sandbox.copy_to("/tmp/stage/0/projects", "s", "/home/agent/.claude")
        self.assertEqual(fake.calls, [
            ["sbx", "cp", "s:/home/agent/.claude/projects", "/tmp/stage/0"],
            ["sbx", "cp", "/tmp/stage/0/projects", "s:/home/agent/.claude"],
        ])

    def test_stored_secret_services_filters_by_scope_and_type(self):
        with patch(RUN, FakeSbx().respond("sbx", "secret", "ls", stdout=SECRETS)):
            self.assertEqual(sandbox.stored_secret_services("agy-sandbox-demo"), {"anthropic", "google"})
