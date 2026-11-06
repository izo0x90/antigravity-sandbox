import json
import unittest

from agy_sandbox import compose
from agy_sandbox.compose import SandboxPlan, delete_plan, load_plan, plan, save_plan
from agy_sandbox.config import AgyConfig, HostBrowserConfig
from agy_sandbox.errors import ConfigError
from agy_sandbox.paths import plan_path
from agy_sandbox.workload import WorkloadRef

from tests.support import isolated_state

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
    def test_round_trip(self):
        with isolated_state():
            p = make_plan(auth_mode="proxy-managed", env=["A=1"], ports=["80:80"])
            save_plan(p)
            loaded = load_plan(p.name)
            self.assertEqual(loaded, SandboxPlan(**{**p.__dict__}))
            self.assertEqual(loaded.workload, WORKLOAD)
            data = json.loads(plan_path(p.name).read_text())
            self.assertEqual(data["schema"], 1)
            self.assertIn("saved_at", data)
            delete_plan(p.name)
            self.assertIsNone(load_plan(p.name))

    def test_missing_plan_is_none(self):
        with isolated_state():
            self.assertIsNone(load_plan("agy-sandbox-nothing"))

    def test_newer_schema_and_corrupt_files_are_config_errors(self):
        with isolated_state():
            path = plan_path("agy-sandbox-x")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"schema": 99}), encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                load_plan("agy-sandbox-x")
            self.assertIn("newer", ctx.exception.message)
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_plan("agy-sandbox-x")
            path.write_text(json.dumps({"schema": 1, "name": "x"}), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_plan("agy-sandbox-x")

    def test_plan_records_the_tool_version(self):
        self.assertEqual(make_plan().agy_version, compose.__version__)
