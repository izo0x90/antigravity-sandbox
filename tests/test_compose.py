import json
import unittest
from unittest.mock import patch

from agy_sandbox import compose
from agy_sandbox.compose import PLAN_PATH, SandboxPlan, load_plan, plan, save_plan
from agy_sandbox.config import AgyConfig, HostBrowserConfig
from agy_sandbox.errors import ConfigError
from agy_sandbox.workload import WorkloadRef

from tests.support import sandbox_files

WORKLOAD = WorkloadRef(dir="/state/kits/x/workload", fingerprint="sha256:abc")


def make_plan(**overrides) -> SandboxPlan:
    cfg = AgyConfig(project_name="demo", agent="claude", kits=["claude", "chrome-devtools"])
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return plan(cfg, workspace="/w", workload=WORKLOAD)


class TestPlan(unittest.TestCase):
    def test_sandbox_local_attaches_agent_and_mixins_only(self):
        p = make_plan()
        self.assertEqual(p.mixin_names(), ["claude", "chrome-devtools"])
        self.assertEqual(p.kit_args, ())
        self.assertEqual(p.auth_mode, "sandbox-local")
        self.assertEqual(p.workspace, "/w")

    def test_proxy_managed_adds_credentials_kits_after_their_agent(self):
        p = make_plan(auth_mode="proxy-managed", kits=["claude", "codex", "chrome-devtools"])
        self.assertEqual(p.mixin_names(), ["claude", "claude-credentials", "codex", "codex-credentials", "chrome-devtools"])
        self.assertTrue(all(m.bundled for m in p.mixins))

    def test_proxy_managed_refuses_two_agents_that_share_a_credential_service(self):
        with self.assertRaises(ConfigError) as ctx:
            make_plan(auth_mode="proxy-managed", kits=["claude", "opencode"])
        self.assertIn("'anthropic'", ctx.exception.message)
        self.assertIn("sandbox-local", ctx.exception.hint)
        make_plan(auth_mode="sandbox-local", kits=["claude", "opencode"])   # fine without credential kits

    def test_agents_without_credentials_kit_get_none(self):
        p = make_plan(auth_mode="proxy-managed", agent="omp", kits=["omp"])
        self.assertEqual(p.mixin_names(), ["omp"])

    def test_host_browser_appends_chrome_host_and_its_port_argument(self):
        p = make_plan(host_browser=HostBrowserConfig(enabled=True, port=9333),
                      kit_args={"agy-credentials.auth_method": "gcp", "chrome-host.host_port": "1"})
        self.assertEqual(p.mixin_names()[-1], "chrome-host")
        self.assertEqual(p.kit_args, ("agy-credentials.auth_method=gcp", "chrome-host.host_port=9333"))

    def test_host_browser_requires_chrome_devtools(self):
        with self.assertRaises(ConfigError) as ctx:
            make_plan(kits=["claude"], host_browser=HostBrowserConfig(enabled=True))
        self.assertIn("kits add chrome-devtools", ctx.exception.hint)

    def test_env_and_ports_pass_through_unchanged(self):
        p = make_plan(env=["A=1", "ANTHROPIC_API_KEY=x"], ports=["3000:3000"])
        self.assertEqual(p.env, ("A=1", "ANTHROPIC_API_KEY=x"))
        self.assertEqual(p.ports, ("3000:3000",))

    def test_duplicate_kits_are_attached_once(self):
        p = make_plan(kits=["claude", "claude", "chrome-devtools"])
        self.assertEqual(p.mixin_names(), ["claude", "chrome-devtools"])


class TestPersistence(unittest.TestCase):
    def test_round_trip_through_the_file_inside_the_sandbox(self):
        with sandbox_files() as files:
            p = make_plan(auth_mode="proxy-managed", env=["A=1"], ports=["80:80"])
            save_plan(p)
            self.assertEqual(list(files), [(p.name, PLAN_PATH)])      # written into that sandbox, nowhere on the host
            loaded = load_plan(p.name)
            self.assertEqual(loaded, SandboxPlan(**{**p.__dict__}))
            self.assertEqual(loaded.workload, WORKLOAD)
            data = json.loads(files[(p.name, PLAN_PATH)])
            self.assertEqual(data["schema"], 1)
            self.assertIn("saved_at", data)

    def test_a_sandbox_without_a_record_is_none(self):
        with sandbox_files():
            self.assertIsNone(load_plan("agy-sandbox-nothing"))

    def test_newer_schema_and_corrupt_records_are_config_errors(self):
        with sandbox_files() as files:
            key = ("agy-sandbox-x", PLAN_PATH)
            files[key] = json.dumps({"schema": 99})
            with self.assertRaises(ConfigError) as ctx:
                load_plan("agy-sandbox-x")
            self.assertIn("newer", ctx.exception.message)
            files[key] = "{not json"
            with self.assertRaises(ConfigError):
                load_plan("agy-sandbox-x")
            files[key] = json.dumps({"schema": 1, "name": "x"})
            with self.assertRaises(ConfigError) as ctx:
                load_plan("agy-sandbox-x")
            self.assertIn("up --rebuild", ctx.exception.hint)

    def test_write_and_read_file_run_through_one_root_exec_each(self):
        import subprocess
        from agy_sandbox import sandbox
        with patch("agy_sandbox.sandbox.exec_script", return_value=subprocess.CompletedProcess([], 0, stdout="{}", stderr="")) as ex:
            sandbox.write_file("box", "/etc/agy-sandbox/plan.json", '{"a": 1}')
            self.assertEqual(ex.call_args.kwargs["user"], "root")
            script = ex.call_args[0][1]
            self.assertIn("mkdir -p", script)
            self.assertIn('{"a": 1}', script)
            self.assertEqual(sandbox.read_file("box", "/etc/agy-sandbox/plan.json"), "{}")
        with patch("agy_sandbox.sandbox.exec_script", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="no such file")):
            self.assertIsNone(sandbox.read_file("box", "/etc/agy-sandbox/plan.json"))

    def test_plan_records_the_tool_version(self):
        self.assertEqual(make_plan().agy_version, compose.__version__)
