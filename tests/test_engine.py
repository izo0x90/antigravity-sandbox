import unittest
from unittest.mock import patch

from agy_sandbox import engine
from agy_sandbox.config import AgyConfig, HostBrowserConfig
from agy_sandbox.constants import PROXY_PLACEHOLDER_ENV
from agy_sandbox.host_browser import HostBrowser, HostBrowserError

BLANKED = [f"{v}=" for v in PROXY_PLACEHOLDER_ENV]

SBX = "agy_sandbox.sandbox."
IMG = "agy_sandbox.images."


def config(**overrides) -> AgyConfig:
    base = dict(project_name="demo", agent="claude", kits=["claude", "chrome-devtools"])
    base.update(overrides)
    return AgyConfig(**base)


class TestUpResume(unittest.TestCase):
    def test_resume_reconciles_ports_and_attaches_without_building(self):
        cfg = config(ports=["3000:3000"], env=["A=1"])
        with patch(SBX + "require_sbx"), patch(SBX + "sandbox_exists", return_value=True), \
             patch(SBX + "sandbox_kit_names", return_value=["claude", "chrome-devtools"]), \
             patch(SBX + "publish_ports") as ports, patch(SBX + "attach", return_value=0) as attach, \
             patch(IMG + "build_project_image") as build:
            self.assertEqual(engine.up(cfg), 0)
        build.assert_not_called()
        ports.assert_called_once_with(cfg.sandbox_name, ["3000:3000"])
        attach.assert_called_once_with(cfg.sandbox_name, env=["A=1"] + BLANKED)

    def test_proxy_managed_session_keeps_placeholders(self):
        self.assertEqual(engine.session_env(config(env=["A=1"], auth_mode="proxy-managed")), ["A=1"])
        self.assertEqual(engine.session_env(config(env=["A=1"])), ["A=1"] + BLANKED)

    def test_resume_hints_at_upgrade_when_kits_drift(self):
        cfg = config(kits=["claude", "opencode"])
        with patch(SBX + "require_sbx"), patch(SBX + "sandbox_exists", return_value=True), \
             patch(SBX + "sandbox_kit_names", return_value=["claude", "chrome-devtools"]), \
             patch(SBX + "publish_ports"), patch(SBX + "attach", return_value=0), patch("builtins.print") as printed:
            engine.up(cfg)
        hints = [c[0][0] for c in printed.call_args_list if "agy-sandbox upgrade" in str(c[0][0])]
        self.assertEqual(len(hints), 1)
        self.assertIn("+opencode", hints[0])
        self.assertIn("-chrome-devtools", hints[0])

    def test_preflight_runs_before_anything(self):
        calls = []
        with patch(SBX + "require_sbx", side_effect=lambda: calls.append("preflight")), \
             patch(SBX + "sandbox_exists", side_effect=lambda n: calls.append("exists") or True), \
             patch(SBX + "sandbox_kit_names", return_value=[]), patch(SBX + "publish_ports"), \
             patch(SBX + "attach", return_value=0):
            engine.up(config(kits=["claude"]))
        self.assertEqual(calls, ["preflight", "exists"])

    def test_existing_sandbox_without_host_kit_needs_rebuild(self):
        cfg = config(host_browser=HostBrowserConfig(enabled=True))
        with patch(SBX + "require_sbx"), patch(SBX + "sandbox_exists", return_value=True), \
             patch(SBX + "sandbox_kit_names", return_value=["claude", "chrome-devtools"]):
            with self.assertRaisesRegex(HostBrowserError, "agy-sandbox upgrade --host-browser"):
                engine.up(cfg)

    def test_existing_host_sandbox_starts_browser(self):
        cfg = config(host_browser=HostBrowserConfig(enabled=True, port=9333))
        with patch(SBX + "require_sbx"), patch(SBX + "sandbox_exists", return_value=True), \
             patch(SBX + "sandbox_kit_names", return_value=["claude", "chrome-devtools", "chrome-host"]), \
             patch(SBX + "publish_ports"), patch(SBX + "attach", return_value=0), \
             patch.object(HostBrowser, "start") as start:
            engine.up(cfg)
        start.assert_called_once()


class TestUpCreate(unittest.TestCase):
    def _create(self, cfg, rebuild=False, loaded=True, secrets=frozenset()):
        with patch(SBX + "require_sbx"), patch(SBX + "sandbox_exists", return_value=False), \
             patch(SBX + "remove_sandbox") as remove, patch(IMG + "build_project_image", return_value="agy-sandbox-demo"), \
             patch(IMG + "image_id", return_value="sha256:abc"), patch(SBX + "template_loaded", return_value=loaded), \
             patch(SBX + "load_template") as load, patch(SBX + "stored_secret_services", return_value=set(secrets)), \
             patch(SBX + "create") as create, patch(SBX + "attach", return_value=0) as attach, \
             patch.object(HostBrowser, "start") as start, patch("builtins.print") as printed, \
             patch("os.getcwd", return_value="/work"):
            code = engine.up(cfg, rebuild=rebuild)
        self.assertEqual(attach.call_args[0][0], cfg.sandbox_name)
        return code, remove, load, create.call_args, start, printed

    def test_create_passes_kits_args_env_ports_and_clone(self):
        cfg = config(env=["A=1"], ports=["3000:3000"], clone=True)
        code, remove, load, call, start, _ = self._create(cfg)
        self.assertEqual(code, 0)
        remove.assert_not_called()
        load.assert_not_called()
        start.assert_not_called()
        args, kwargs = call
        self.assertEqual(args[:3], (cfg.sandbox_name, "agy-sandbox-demo:latest", "/work"))
        self.assertEqual([k.rsplit("/", 1)[-1] for k in kwargs["kits"]], ["claude", "chrome-devtools"])
        self.assertEqual(kwargs["kit_args"], ["claude.auth_mode=sandbox-local"])
        self.assertEqual(kwargs["env"], ["A=1"] + BLANKED)
        self.assertEqual(kwargs["ports"], ["3000:3000"])
        self.assertTrue(kwargs["clone"])

    def test_rebuild_removes_and_reloads_template(self):
        _, remove, load, _, _, _ = self._create(config(), rebuild=True)
        remove.assert_called_once_with("agy-sandbox-demo")
        load.assert_called_once_with("agy-sandbox-demo")

    def test_unloaded_template_is_loaded(self):
        _, _, load, _, _, _ = self._create(config(), loaded=False)
        load.assert_called_once()

    def test_proxy_managed_attaches_overlay_and_reports_missing_secrets(self):
        cfg = config(kits=["claude", "agy"], auth_mode="proxy-managed")
        _, _, _, call, _, printed = self._create(cfg, secrets={"anthropic"})
        kwargs = call[1]
        self.assertEqual([k.rsplit("/", 1)[-1] for k in kwargs["kits"]], ["claude", "agy", "agy-proxy"])
        self.assertIn("claude.auth_mode=proxy-managed", kwargs["kit_args"])
        self.assertIn("agy.auth_mode=proxy-managed", kwargs["kit_args"])
        output = "\n".join(str(c[0][0]) for c in printed.call_args_list if c[0])
        self.assertIn("no sbx secret is stored for: google", output)
        self.assertIn(f"sbx secret set google --sandbox {cfg.sandbox_name}", output)
        self.assertNotIn("anthropic", output.split("stored for:")[1].splitlines()[0])

    def test_sandbox_local_never_checks_secrets(self):
        with patch(SBX + "stored_secret_services") as stored:
            self.assertEqual(engine.missing_proxy_secrets(config()), set())
        stored.assert_not_called()

    def test_host_browser_adds_kit_arg_and_starts_browser(self):
        cfg = config(host_browser=HostBrowserConfig(enabled=True, port=9333))
        _, _, _, call, start, _ = self._create(cfg)
        kwargs = call[1]
        self.assertEqual(kwargs["kits"][-1].rsplit("/", 1)[-1], "chrome-host")
        self.assertIn("chrome-host.host_port=9333", kwargs["kit_args"])
        start.assert_called_once()

    def test_host_browser_requires_chrome_devtools_kit(self):
        cfg = config(kits=["claude"], host_browser=HostBrowserConfig(enabled=True))
        with self.assertRaisesRegex(HostBrowserError, "kits add chrome-devtools"):
            engine.up(cfg)


class TestUpgrade(unittest.TestCase):
    INSPECT = {
        "name": "agy-sandbox-demo",
        "kits": ["/pkg/kits/claude", "/pkg/kits/chrome-devtools"],
        "workspace": "/Users/me/demo",
        "source_repo_dir": "/Users/me/demo",
    }

    def _upgrade(self, cfg, inspect=None, create_error=None):
        calls = []

        def record(label):
            def _rec(*a, **kw):
                calls.append((label, a, kw))
                if label == "create" and create_error:
                    raise create_error
                return 0 if label == "attach" else subprocess_ok()
            return _rec

        def subprocess_ok():
            import subprocess
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch(SBX + "require_sbx"), patch(SBX + "sandbox_exists", return_value=True), \
             patch(SBX + "inspect_sandbox", return_value=inspect or self.INSPECT), \
             patch("agy_sandbox.engine.kit_volume_paths", return_value=["/home/agent/.claude/projects"]), \
             patch(SBX + "exec_script", side_effect=record("exec")), patch(SBX + "copy_from_sandbox", side_effect=record("cp_out")), \
             patch(SBX + "stop_sandbox", side_effect=record("stop")), patch(SBX + "save_template", side_effect=record("save")), \
             patch(SBX + "remove_sandbox", side_effect=record("rm")), patch(SBX + "create", side_effect=record("create")), \
             patch(SBX + "copy_to_sandbox", side_effect=record("cp_in")), patch(SBX + "list_templates", return_value=[
                 {"repository": "docker.io/library/agy-sandbox-demo", "tag": "upgrade-20200101-000000"},
                 {"repository": "docker.io/library/agy-sandbox-demo", "tag": "latest"},
                 {"repository": "docker.io/library/agy-sandbox-other", "tag": "upgrade-20200101-000000"},
             ]), patch(SBX + "remove_template", side_effect=record("rm_template")), \
             patch(SBX + "stored_secret_services", return_value=set()), patch(SBX + "attach", side_effect=record("attach")), \
             patch("agy_sandbox.engine.time.strftime", return_value="20260101-120000"), \
             patch("agy_sandbox.engine.shutil.rmtree"), patch("builtins.print"):
            code = engine.upgrade(cfg)
        return code, calls

    def test_upgrade_order_carries_state_and_keeps_snapshot(self):
        cfg = config(kits=["claude", "opencode"], clone=True, env=["A=1"], ports=["3000:3000"])
        code, calls = self._upgrade(cfg)
        self.assertEqual(code, 0)
        labels = [c[0] for c in calls]
        # carry out (exec true, cp workspace + volume), snapshot, rm, create, restore, prune, attach
        self.assertEqual(labels[:4], ["exec", "cp_out", "cp_out", "stop"])
        self.assertEqual(labels[4:7], ["save", "rm", "create"])
        self.assertEqual(labels[7:13], ["exec", "cp_in", "exec"] * 2)
        self.assertEqual(labels[13:], ["exec", "rm_template", "attach"])  # binary check, prune, attach

        cp_out = [c for c in calls if c[0] == "cp_out"]
        self.assertEqual(cp_out[0][1][:2], ("agy-sandbox-demo", "/Users/me/demo"))
        self.assertEqual(cp_out[1][1][:2], ("agy-sandbox-demo", "/home/agent/.claude/projects"))
        save = next(c for c in calls if c[0] == "save")
        self.assertEqual(save[1], ("agy-sandbox-demo", "agy-sandbox-demo:upgrade-20260101-120000"))
        create = next(c for c in calls if c[0] == "create")
        self.assertEqual(create[1][:3], ("agy-sandbox-demo", "agy-sandbox-demo:upgrade-20260101-120000", "/Users/me/demo"))
        self.assertEqual([k.rsplit("/", 1)[-1] for k in create[2]["kits"]], ["claude", "opencode"])
        self.assertEqual(create[2]["env"], ["A=1"] + BLANKED)
        self.assertEqual(create[2]["ports"], ["3000:3000"])
        self.assertTrue(create[2]["clone"])
        cp_in = [c for c in calls if c[0] == "cp_in"]
        self.assertTrue(cp_in[0][1][0].endswith("/0/demo"))
        self.assertEqual(cp_in[0][1][1:], ("agy-sandbox-demo", "/Users/me"))
        self.assertEqual(cp_in[1][1][1:], ("agy-sandbox-demo", "/home/agent/.claude"))
        restores = [c for c in calls if c[0] == "exec" and c[2].get("user") == "root"]
        self.assertIn("find '/Users/me/demo' -mindepth 1 -delete", restores[0][1][1])
        self.assertIn("chown -R agent:agent '/Users/me/demo'", restores[1][1][1])
        prune = next(c for c in calls if c[0] == "rm_template")
        self.assertEqual(prune[1], ("agy-sandbox-demo:upgrade-20200101-000000",))

    def test_bind_mount_sandbox_skips_workspace_carry(self):
        inspect = {**self.INSPECT, "source_repo_dir": ""}
        _, calls = self._upgrade(config(kits=["claude"]), inspect=inspect)
        cp_out = [c for c in calls if c[0] == "cp_out"]
        self.assertEqual([c[1][1] for c in cp_out], ["/home/agent/.claude/projects"])
        create = next(c for c in calls if c[0] == "create")
        self.assertFalse(create[2]["clone"])

    def test_failed_create_leaves_snapshot_and_raises(self):
        from agy_sandbox.sandbox import SandboxError
        with self.assertRaises(SandboxError):
            self._upgrade(config(kits=["claude"]), create_error=SandboxError("boom"))

    def test_upgrade_requires_existing_sandbox(self):
        from agy_sandbox.sandbox import SandboxError
        with patch(SBX + "require_sbx"), patch(SBX + "sandbox_exists", return_value=False):
            with self.assertRaisesRegex(SandboxError, "agy-sandbox up"):
                engine.upgrade(config())

    def test_missing_agent_binary_warning(self):
        import subprocess
        res = subprocess.CompletedProcess(args=[], returncode=0, stdout="opencode\n", stderr="")
        with patch(SBX + "exec_script", return_value=res) as ex, patch("builtins.print") as printed:
            engine.warn_missing_agent_binaries(config(kits=["claude", "opencode"]))
        self.assertIn("command -v claude", ex.call_args[0][1])
        self.assertIn("opencode", printed.call_args[0][0])
        self.assertNotIn("claude,", printed.call_args[0][0])

    def test_kit_drift(self):
        added, removed = engine.kit_drift(config(kits=["claude", "opencode"]), ["claude", "chrome-devtools"])
        self.assertEqual((added, removed), (["opencode"], ["chrome-devtools"]))


class TestDown(unittest.TestCase):
    def test_down_removes_and_stops_browser(self):
        cfg = config()
        with patch(SBX + "require_sbx"), patch(SBX + "sandbox_exists", return_value=True), \
             patch(SBX + "remove_sandbox") as remove, patch.object(HostBrowser, "stop") as stop:
            engine.down(cfg)
        remove.assert_called_once_with(cfg.sandbox_name)
        stop.assert_called_once()

    def test_down_on_missing_sandbox(self):
        with patch(SBX + "require_sbx"), patch(SBX + "sandbox_exists", return_value=False), \
             patch(SBX + "remove_sandbox") as remove, patch.object(HostBrowser, "stop"):
            engine.down(config())
        remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
