import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agy_sandbox import compose, engine, upgrade
from agy_sandbox.compose import SandboxPlan, diff
from agy_sandbox.config import AgyConfig, HostBrowserConfig
from agy_sandbox.errors import SandboxError, UpgradeRefused
from agy_sandbox.kits import KitRef
from agy_sandbox.sandbox import SandboxInfo, SbxVersion
from agy_sandbox.workload import WorkloadRef

from tests.support import FakeSbx, captured_console, isolated_state, messages_containing

SBX = "agy_sandbox.sandbox."


def config(**overrides) -> AgyConfig:
    base = dict(project_name="demo", agent="claude", kits=["claude", "chrome-devtools"])
    base.update(overrides)
    return AgyConfig(**base)


def plan_of(**overrides) -> SandboxPlan:
    base = dict(
        name="agy-sandbox-demo", workspace="/w", clone=False, auth_mode="sandbox-local",
        workload=WorkloadRef("/state/workload", "sha256:a"),
        mixins=(KitRef("claude", "/k/claude", True), KitRef("chrome-devtools", "/k/chrome-devtools", True)),
        kit_args=(), env=(), ports=(),
    )
    base.update(overrides)
    return SandboxPlan(**base)


class TestDiff(unittest.TestCase):
    def test_no_change(self):
        d = diff(plan_of(), plan_of())
        self.assertTrue(d.empty)
        self.assertTrue(d.in_place)

    def test_additions_are_in_place(self):
        wanted = plan_of(mixins=plan_of().mixins + (KitRef("opencode", "/k/opencode", True),), env=("A=1",), ports=("80:80",))
        d = diff(plan_of(), wanted)
        self.assertEqual([m.name for m in d.added], ["opencode"])
        self.assertTrue(d.in_place)
        self.assertEqual(d.reasons(), [])

    def test_removals_kit_args_workload_and_clone_block_in_place(self):
        saved = plan_of(kit_args=("chrome-host.host_port=9222", "shared=1"))
        wanted = plan_of(mixins=(KitRef("claude", "/k/claude", True),), kit_args=("claude.x=1", "shared=2"),
                         workload=WorkloadRef("/state/workload", "sha256:b"), clone=True, auth_mode="proxy-managed")
        d = diff(saved, wanted)
        self.assertEqual(d.removed, ("chrome-devtools",))
        self.assertEqual(d.kit_args_changed, ("claude",))
        self.assertTrue(d.workload_changed and d.clone_changed and d.auth_mode_changed)
        self.assertFalse(d.in_place)
        reasons = d.reasons()
        self.assertEqual(len(reasons), 5)
        self.assertTrue(reasons[0].startswith("login mode"))
        self.assertIn("kit removed: chrome-devtools", reasons)

    def test_kit_args_for_other_kits_do_not_count_but_unscoped_ones_do(self):
        d = diff(plan_of(kit_args=("chrome-host.host_port=9222",)), plan_of(kit_args=("chrome-host.host_port=9333",)))
        self.assertTrue(d.in_place)   # chrome-host is not attached in either plan
        d = diff(plan_of(), plan_of(kit_args=("shared=1",)))
        self.assertEqual(d.kit_args_changed, ("chrome-devtools", "claude"))   # an unscoped arg reaches every attached kit

    def test_same_kit_from_a_different_source_is_a_replace(self):
        d = diff(plan_of(), plan_of(mixins=(KitRef("claude", "/elsewhere/claude", False), plan_of().mixins[1])))
        self.assertEqual([m.name for m in d.added], ["claude"])
        self.assertEqual(d.removed, ("claude",))


class UpgradeBase(unittest.TestCase):
    def setUp(self):
        self.state = isolated_state()
        self.state.__enter__()
        self.cwd = tempfile.TemporaryDirectory()
        self.console = captured_console()
        self.out = self.console.__enter__()
        self.patches = [
            patch(SBX + "require", return_value=SbxVersion(0, 50, 1)),
            patch(SBX + "exists", return_value=True),
            patch(SBX + "attach", return_value=0),
            patch(SBX + "stored_secret_services", return_value=set()),
            patch(SBX + "publish_ports"),
            patch(SBX + "kit_add"),
        ]
        self.mocks = {p.attribute: p.start() for p in self.patches}

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.console.__exit__(None, None, None)
        self.cwd.cleanup()
        self.state.__exit__(None, None, None)

    def save(self, cfg: AgyConfig) -> SandboxPlan:
        plan = engine.desired_plan(cfg, Path(self.cwd.name))
        compose.save_plan(plan)
        return plan


class TestUpgrade(UpgradeBase):
    def test_adds_kits_in_order_with_their_arguments_and_saves_the_new_plan(self):
        self.save(config(kits=["claude"], auth_mode="proxy-managed"))
        cfg = config(kits=["claude", "codex", "chrome-devtools"], auth_mode="proxy-managed",
                     host_browser=HostBrowserConfig(enabled=True, port=9333), kit_args={"codex-credentials.x": "1"})
        with patch("agy_sandbox.upgrade.host_browser_for") as browser:
            self.assertEqual(upgrade.upgrade(cfg, Path(self.cwd.name)), 0)
        added = [(c[0][1].name, c[0][2]) for c in self.mocks["kit_add"].call_args_list]
        self.assertEqual(added, [
            ("codex", []),
            ("codex-credentials", ["codex-credentials.x=1"]),
            ("chrome-devtools", []),
            ("chrome-host", ["chrome-host.host_port=9333"]),
        ])
        self.assertEqual(compose.load_plan("agy-sandbox-demo").mixin_names(),
                         ["claude", "claude-credentials", "codex", "codex-credentials", "chrome-devtools", "chrome-host"])
        browser.return_value.start.assert_called_once()

    def test_login_mode_change_is_refused(self):
        self.save(config(kits=["claude"]))
        with self.assertRaises(UpgradeRefused) as ctx:
            upgrade.upgrade(config(kits=["claude"], auth_mode="proxy-managed"), Path(self.cwd.name))
        self.assertIn("login mode", ctx.exception.reasons[0])
        self.mocks["kit_add"].assert_not_called()

    def test_refuses_removals_before_touching_the_sandbox(self):
        self.save(config(kits=["claude", "chrome-devtools"]))
        with self.assertRaises(UpgradeRefused) as ctx:
            upgrade.upgrade(config(kits=["claude"]), Path(self.cwd.name))
        self.assertEqual(ctx.exception.reasons, ["kit removed: chrome-devtools"])
        self.assertIn("agy-sandbox recreate", ctx.exception.hint)
        self.mocks["kit_add"].assert_not_called()
        self.mocks["publish_ports"].assert_not_called()

    def test_refuses_without_saved_plan_and_below_minimum_version(self):
        with self.assertRaises(UpgradeRefused) as ctx:
            upgrade.upgrade(config(), Path(self.cwd.name))
        self.assertIn("older agy-sandbox", ctx.exception.reasons[0])
        self.mocks["require"].return_value = SbxVersion(0, 46, 0)
        with self.assertRaises(UpgradeRefused) as ctx:
            upgrade.upgrade(config(), Path(self.cwd.name))
        self.assertIn("0.48", ctx.exception.reasons[0])
        self.assertEqual(ctx.exception.hint, "brew upgrade --cask sbx")

    def test_env_and_ports_only_is_applied_without_kit_add(self):
        self.save(config())
        self.assertEqual(upgrade.upgrade(config(env=["A=1"], ports=["80:80"]), Path(self.cwd.name)), 0)
        self.mocks["kit_add"].assert_not_called()
        self.mocks["publish_ports"].assert_called_once_with("agy-sandbox-demo", ("80:80",))
        self.mocks["attach"].assert_called_once_with("agy-sandbox-demo", env=("A=1",))

    def test_missing_sandbox_is_refused(self):
        self.mocks["exists"].return_value = False
        with self.assertRaises(UpgradeRefused):
            upgrade.upgrade(config(), Path(self.cwd.name))


class TestRecreate(UpgradeBase):
    def run_recreate(self, cfg, fake=None):
        fake = fake or FakeSbx()
        patches = [
            patch(SBX + "exec_script"), patch(SBX + "copy_from"), patch(SBX + "copy_to"), patch(SBX + "stop"),
            patch(SBX + "save_template"), patch(SBX + "remove"), patch(SBX + "create_from_template"),
            patch(SBX + "list_templates", return_value=[
                {"repository": "docker.io/library/agy-sandbox-demo", "tag": "recreate-20200101-000000"},
                {"repository": "docker.io/library/agy-sandbox-other", "tag": "recreate-20200101-000000"},
            ]),
            patch(SBX + "remove_template"),
        ]
        mocks = {p.attribute: p.start() for p in patches}
        try:
            code = upgrade.recreate(cfg, Path(self.cwd.name))
        finally:
            for p in patches:
                p.stop()
        return code, mocks

    def test_carries_volumes_snapshots_and_restores_in_order(self):
        self.save(config(kits=["claude", "chrome-devtools"]))
        cfg = config(kits=["claude"])
        code, m = self.run_recreate(cfg)
        self.assertEqual(code, 0)
        carried = [c[0][1] for c in m["copy_from"].call_args_list]
        self.assertEqual(carried[0], "/home/agent/.claude/projects")
        self.assertEqual(len(carried), 5)
        m["stop"].assert_called_once_with("agy-sandbox-demo")
        tag = m["save_template"].call_args[0][1]
        self.assertTrue(tag.startswith("agy-sandbox-demo:recreate-"))
        m["remove"].assert_called_once_with("agy-sandbox-demo")
        plan, template = m["create_from_template"].call_args[0]
        self.assertEqual((plan.mixin_names(), template), (["claude"], tag))
        restored = [c[0][2] for c in m["copy_to"].call_args_list]
        self.assertEqual(restored[0], "/home/agent/.claude")
        m["remove_template"].assert_called_once_with("agy-sandbox-demo:recreate-20200101-000000")
        self.assertEqual(compose.load_plan("agy-sandbox-demo").mixin_names(), ["claude"])
        self.assertTrue(messages_containing(self.out, "Snapshot kept as"))

    def test_clone_mode_is_kept_and_its_workspace_carried(self):
        self.save(config(clone=True))
        code, m = self.run_recreate(config(clone=False))
        carried = [c[0][1] for c in m["copy_from"].call_args_list]
        self.assertEqual(carried[0], os.path.abspath(self.cwd.name))
        self.assertTrue(m["create_from_template"].call_args[0][0].clone)
        self.assertTrue(messages_containing(self.out, "clone mode is fixed"))

    def test_failed_recreate_prints_the_restore_command_and_raises(self):
        self.save(config())
        with patch(SBX + "exec_script"), patch(SBX + "copy_from"), patch(SBX + "stop"), patch(SBX + "save_template"), \
             patch(SBX + "remove"), patch(SBX + "create_from_template", side_effect=SandboxError("nope")), \
             self.assertRaises(SandboxError):
            upgrade.recreate(config(), Path(self.cwd.name))
        restore = messages_containing(self.out, "Restore it with: sbx create --name agy-sandbox-demo --template")
        self.assertEqual(len(restore), 1)
        self.assertIsNotNone(compose.load_plan("agy-sandbox-demo"))   # the old plan is kept for the retry

    def test_without_saved_plan_falls_back_to_inspect(self):
        with patch(SBX + "inspect", return_value=SandboxInfo("agy-sandbox-demo", "stopped", (), "/old", True)):
            code, m = self.run_recreate(config())
        self.assertEqual(code, 0)
        plan = m["create_from_template"].call_args[0][0]
        self.assertEqual((plan.clone, plan.workspace), (True, "/old"))
        self.assertTrue(messages_containing(self.out, "no saved plan"))
