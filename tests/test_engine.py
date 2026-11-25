import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agy_sandbox import compose, engine
from agy_sandbox.config import AgyConfig, HostBrowserConfig
from agy_sandbox.errors import ConfigError
from agy_sandbox.kits import KitRef
from agy_sandbox.sandbox import SbxVersion
from agy_sandbox.workload import WorkloadRef

from tests.support import captured_console, isolated_state, messages_containing

SBX = "agy_sandbox.sandbox."
HB = "agy_sandbox.engine.host_browser_for"
FIXED_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
V1 = "agy-sandbox-demo-aaaaaaaaaaaaaaaaaaaa-v1"                          # the name a fresh project's first sandbox gets


def config(**overrides) -> AgyConfig:
    base = dict(project_name="demo", agent="claude", kits=["claude", "chrome-devtools"])
    base.update(overrides)
    return AgyConfig(**base)


def preflight(exists: bool):
    return (patch(SBX + "require", return_value=SbxVersion(0, 50, 1)), patch(SBX + "exists", return_value=exists))


def fixed_id():
    return patch("agy_sandbox.engine.new_sandbox_id", return_value=FIXED_ID)


class TestCreate(unittest.TestCase):
    def test_create_writes_workload_saves_plan_and_attaches(self):
        cfg = config(env=["A=1"], ports=["3000:3000"])
        with isolated_state() as state, tempfile.TemporaryDirectory() as cwd, captured_console(), \
             preflight(False)[0], preflight(False)[1], fixed_id(), \
             patch(SBX + "create") as create, patch(SBX + "attach", return_value=0) as attach, \
             patch(SBX + "stored_secret_services", return_value=set()):
            self.assertEqual(engine.up(cfg, cwd=Path(cwd)), 0)
            plan = create.call_args[0][0]
            self.assertEqual(plan.name, V1)                 # a new project is created under an identity, as version 1
            self.assertEqual((cfg.sandbox_id, cfg.sandbox_version), (FIXED_ID, 1))
            self.assertEqual(plan.mixin_names(), ["claude", "chrome-devtools"])
            self.assertEqual(plan.env, ("A=1",))
            self.assertTrue((state / "kits" / V1 / "workload" / "workload.yaml").is_file())
            self.assertEqual(compose.load_plan(V1), plan)
        attach.assert_called_once_with(V1, env=("A=1",))

    def test_first_create_records_the_identity_in_the_config_file_keeping_its_layout(self):
        cfg = config()
        with isolated_state(), tempfile.TemporaryDirectory() as cwd, captured_console() as out, \
             preflight(False)[0], preflight(False)[1], fixed_id(), patch(SBX + "create"), \
             patch(SBX + "attach", return_value=0), patch(SBX + "stored_secret_services", return_value=set()):
            path = Path(cwd) / "agy.yaml"
            path.write_text("# my box\nprofile: default\nproject_name: demo   # keep\nagent: claude\n")
            engine.up(cfg, cwd=Path(cwd), config_path=str(path))
            self.assertEqual(path.read_text(), "# my box\nprofile: default\nproject_name: demo   # keep\n"
                                               f"sandbox_id: {FIXED_ID}\nsandbox_version: 1\nagent: claude\n")
        self.assertTrue(messages_containing(out, "Gave this project a sandbox id"))

    def test_a_project_with_an_identity_is_created_under_its_current_version(self):
        cfg = config(sandbox_id=FIXED_ID, sandbox_version=3)
        with isolated_state(), tempfile.TemporaryDirectory() as cwd, captured_console(), \
             preflight(False)[0], preflight(False)[1], patch(SBX + "create") as create, \
             patch(SBX + "attach", return_value=0), patch(SBX + "stored_secret_services", return_value=set()):
            engine.up(cfg, cwd=Path(cwd))
        self.assertEqual(create.call_args[0][0].name, V1[:-1] + "3")

    def test_a_pre_identity_sandbox_is_resumed_and_the_file_is_left_alone(self):
        cfg = config()
        with isolated_state(), tempfile.TemporaryDirectory() as cwd, captured_console(), \
             preflight(True)[0], preflight(True)[1], patch(SBX + "publish_ports"), \
             patch(SBX + "attach", return_value=0) as attach, patch(SBX + "create") as create:
            path = Path(cwd) / "agy.yaml"
            path.write_text("project_name: demo\n")
            engine.up(cfg, cwd=Path(cwd), config_path=str(path))
            self.assertEqual(path.read_text(), "project_name: demo\n")
        create.assert_not_called()
        attach.assert_called_once_with("agy-sandbox-demo", env=[])   # the legacy name, as before
        self.assertIsNone(cfg.sandbox_id)

    def test_proxy_managed_reports_missing_secrets_with_the_exact_command(self):
        cfg = config(auth_mode="proxy-managed", kits=["claude", "codex"])
        with isolated_state(), tempfile.TemporaryDirectory() as cwd, captured_console() as out, \
             preflight(False)[0], preflight(False)[1], fixed_id(), patch(SBX + "create"), patch(SBX + "attach", return_value=0), \
             patch(SBX + "stored_secret_services", return_value={"anthropic"}):
            engine.up(cfg, cwd=Path(cwd))
        self.assertTrue(messages_containing(out, f"sbx secret set openai --sandbox {V1}"))
        self.assertFalse(messages_containing(out, "sbx secret set anthropic"))

    def test_rebuild_removes_sandbox_and_plan_first(self):
        cfg = config()
        with isolated_state(), tempfile.TemporaryDirectory() as cwd, captured_console(), \
             preflight(True)[0], preflight(True)[1], fixed_id(), patch(SBX + "remove") as remove, \
             patch(SBX + "create") as create, patch(SBX + "attach", return_value=0), patch(SBX + "stored_secret_services", return_value=set()):
            engine.up(cfg, rebuild=True, cwd=Path(cwd))
        remove.assert_called_once_with("agy-sandbox-demo")        # the pre-identity sandbox goes...
        self.assertEqual(create.call_args[0][0].name, V1)          # ...and its replacement gets an identity

    def test_preflight_runs_before_anything_and_host_browser_is_validated_first(self):
        calls = []
        with patch(SBX + "require", side_effect=lambda: calls.append("require") or SbxVersion(0, 50, 1)), \
             patch(SBX + "exists", side_effect=lambda n: calls.append("exists") or True), \
             patch("agy_sandbox.engine.resume", return_value=0):
            engine.up(config(), cwd=Path("/w"))
        self.assertEqual(calls, ["require", "exists"])
        with self.assertRaises(ConfigError), patch(SBX + "require") as require:
            engine.up(config(kits=["claude"], host_browser=HostBrowserConfig(enabled=True)), cwd=Path("/w"))
        require.assert_not_called()


class TestResume(unittest.TestCase):
    def saved_plan(self, cfg, cwd, **overrides):
        plan = engine.desired_plan(cfg, Path(cwd))
        plan = compose.SandboxPlan(**{**plan.__dict__, **overrides})
        compose.save_plan(plan)
        return plan

    def test_no_attach_does_the_work_and_prints_how_to_attach(self):
        cfg = AgyConfig(project_name="demo", kits=["claude"], env=["A=1"])
        with isolated_state(), tempfile.TemporaryDirectory() as cwd, \
             preflight(False)[0], preflight(False)[1], patch(SBX + "create") as create, patch(SBX + "attach") as attach, \
             patch(SBX + "stored_secret_services", return_value=set()), captured_console() as out:
            self.assertEqual(engine.up(cfg, cwd=Path(cwd), attach=False), 0)
        create.assert_called_once()
        attach.assert_not_called()
        self.assertTrue(messages_containing(out, "Attach with: agy-sandbox up"))

    def test_resume_reconciles_ports_and_attaches_without_creating(self):
        cfg = config(ports=["3000:3000"], env=["A=1"])
        with isolated_state(), tempfile.TemporaryDirectory() as cwd, captured_console() as out, \
             preflight(True)[0], preflight(True)[1], patch(SBX + "publish_ports") as ports, \
             patch(SBX + "attach", return_value=0) as attach, patch(SBX + "create") as create:
            self.saved_plan(cfg, cwd)
            self.assertEqual(engine.up(cfg, cwd=Path(cwd)), 0)
        create.assert_not_called()
        ports.assert_called_once_with("agy-sandbox-demo", ["3000:3000"])
        attach.assert_called_once_with("agy-sandbox-demo", env=["A=1"])
        self.assertFalse(messages_containing(out, "differs"))

    def test_resume_hints_upgrade_for_additions_and_recreate_for_removals(self):
        with isolated_state(), tempfile.TemporaryDirectory() as cwd, captured_console() as out, \
             preflight(True)[0], preflight(True)[1], patch(SBX + "publish_ports"), patch(SBX + "attach", return_value=0):
            self.saved_plan(config(kits=["claude"]), cwd)
            engine.up(config(kits=["claude", "opencode"]), cwd=Path(cwd))
            self.saved_plan(config(kits=["claude", "chrome-devtools"]), cwd)
            engine.up(config(kits=["claude"]), cwd=Path(cwd))
        hints = messages_containing(out, "differs from this sandbox")
        self.assertEqual(len(hints), 2)
        self.assertIn("+opencode", hints[0])
        self.assertTrue(hints[0].endswith("agy-sandbox upgrade"))
        self.assertIn("kit removed: chrome-devtools", hints[1])
        self.assertTrue(hints[1].endswith("agy-sandbox recreate"))

    def test_resume_never_rewrites_the_workload_kit_and_rebuild_hint_wins(self):
        with isolated_state(), tempfile.TemporaryDirectory() as cwd, captured_console() as out, \
             preflight(True)[0], preflight(True)[1], patch(SBX + "publish_ports"), patch(SBX + "attach", return_value=0):
            plan = self.saved_plan(config(), cwd)
            before = (Path(plan.workload.dir) / "workload.dockerfile").read_text()
            engine.up(config(base_image="docker.io/other:1"), cwd=Path(cwd))
            self.assertEqual((Path(plan.workload.dir) / "workload.dockerfile").read_text(), before)
        hints = messages_containing(out, "differs from this sandbox")
        self.assertTrue(hints[0].endswith("agy-sandbox up --rebuild"))

    def test_down_removes_the_generated_workload_kit(self):
        cfg = config()
        with isolated_state() as state, tempfile.TemporaryDirectory() as cwd, captured_console(), \
             preflight(True)[0], preflight(True)[1], patch(SBX + "remove"), patch(HB):
            engine.desired_plan(cfg, Path(cwd))
            self.assertTrue((state / "kits" / cfg.sandbox_name / "workload").is_dir())
            engine.down(cfg)
            self.assertFalse((state / "kits" / cfg.sandbox_name).exists())

    def test_resume_without_saved_plan_warns_once(self):
        with isolated_state(), tempfile.TemporaryDirectory() as cwd, captured_console() as out, \
             preflight(True)[0], preflight(True)[1], patch(SBX + "publish_ports"), patch(SBX + "attach", return_value=0):
            engine.up(config(), cwd=Path(cwd))
        self.assertEqual(len(messages_containing(out, "older agy-sandbox")), 1)

    def test_resume_with_host_browser_starts_it_and_warns_when_kit_missing(self):
        cfg = config(host_browser=HostBrowserConfig(enabled=True))
        with isolated_state(), tempfile.TemporaryDirectory() as cwd, captured_console() as out, \
             preflight(True)[0], preflight(True)[1], patch(SBX + "publish_ports"), patch(SBX + "attach", return_value=0), \
             patch(HB) as browser:
            self.saved_plan(config(), cwd)   # created without chrome-host
            engine.up(cfg, cwd=Path(cwd))
        browser.return_value.start.assert_called_once()
        self.assertTrue(messages_containing(out, "upgrade --host-browser"))


class TestDown(unittest.TestCase):
    def test_down_removes_sandbox_plan_and_browser(self):
        cfg = config()
        with isolated_state(), captured_console(), preflight(True)[0], preflight(True)[1], \
             patch(SBX + "remove") as remove, patch(HB) as browser:
            compose.save_plan(compose.SandboxPlan(
                name=cfg.sandbox_name, workspace="/w", clone=False, auth_mode="sandbox-local",
                workload=WorkloadRef("/x", "sha256:1"), mixins=(KitRef("claude", "/k", True),), kit_args=(), env=(), ports=()))
            engine.down(cfg)
            self.assertIsNone(compose.load_plan(cfg.sandbox_name))
        remove.assert_called_once_with("agy-sandbox-demo")
        browser.return_value.stop.assert_called_once()

    def test_down_on_missing_sandbox_is_informational(self):
        with isolated_state(), captured_console() as out, preflight(False)[0], preflight(False)[1], \
             patch(SBX + "remove") as remove, patch(HB):
            engine.down(config())
        remove.assert_not_called()
        self.assertTrue(messages_containing(out, "does not exist"))
