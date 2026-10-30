import unittest
from unittest.mock import patch

from agy_sandbox import engine
from agy_sandbox.config import AgyConfig, HostBrowserConfig
from agy_sandbox.host_browser import HostBrowser, HostBrowserError

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
        attach.assert_called_once_with(cfg.sandbox_name, env=["A=1"])

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
            with self.assertRaisesRegex(HostBrowserError, "--host-browser --rebuild"):
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
             patch(SBX + "create_and_attach", return_value=0) as create, patch.object(HostBrowser, "start") as start, \
             patch("builtins.print") as printed:
            code = engine.up(cfg, rebuild=rebuild)
        return code, remove, load, create.call_args, start, printed

    def test_create_passes_kits_args_env_ports_and_clone(self):
        cfg = config(env=["A=1"], ports=["3000:3000"], clone=True)
        code, remove, load, call, start, _ = self._create(cfg)
        self.assertEqual(code, 0)
        remove.assert_not_called()
        load.assert_not_called()
        start.assert_not_called()
        args, kwargs = call
        self.assertEqual(args[:2], (cfg.sandbox_name, "agy-sandbox-demo"))
        self.assertEqual([k.rsplit("/", 1)[-1] for k in kwargs["kits"]], ["claude", "chrome-devtools"])
        self.assertEqual(kwargs["kit_args"], ["claude.auth_mode=sandbox-local"])
        self.assertEqual(kwargs["env"], ["A=1"])
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
