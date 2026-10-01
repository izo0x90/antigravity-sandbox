import importlib.util
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agy_sandbox.config import AgyConfig, HostBrowserConfig, load_config, save_config
from agy_sandbox.constants import AGY_MIXIN_KIT_URL, DEFAULT_SBX_KIT_URL, KIT_CHROME_DEVTOOLS, KIT_CHROME_HOST
from agy_sandbox.engine import (
    host_browser_kit_args,
    run_up,
    check_existing_sandbox_host_browser,
    validate_host_browser,
)
from agy_sandbox.host_browser import HostBrowser, HostBrowserError
from agy_sandbox.runners.sbx import resolve_run_kits

RELAY_PATH = (
    Path(__file__).resolve().parent.parent
    / "src/agy_sandbox/kits/chrome-host/files/home/.local/share/agy-sandbox/chrome_host_relay.py"
)


def load_relay_module():
    spec = importlib.util.spec_from_file_location("chrome_host_relay", RELAY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sbx_config(agent="claude", kits=None, host_browser=False) -> AgyConfig:
    return AgyConfig.from_dict(
        {
            "project_name": "demo",
            "agent": agent,
            "sbx": {
                "enabled": True,
                "agent": agent,
                "kits": list(kits if kits is not None else [KIT_CHROME_DEVTOOLS]),
                "host_browser": {"enabled": host_browser, "port": 9333},
            },
        }
    )


class TestHostBrowserConfig(unittest.TestCase):
    def test_defaults_when_missing(self):
        self.assertEqual(HostBrowserConfig.from_dict(None), HostBrowserConfig())

    def test_saved_only_when_non_default(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "agy.yaml")
            config = AgyConfig.from_dict({"sbx": {"enabled": True}})
            save_config(config, path)
            self.assertNotIn("host_browser", Path(path).read_text())

            config.sbx.host_browser = HostBrowserConfig(enabled=True, port=9333, headless=True)
            save_config(config, path)
            self.assertEqual(load_config(path).sbx.host_browser, HostBrowserConfig(enabled=True, port=9333, headless=True))


class TestHostBrowser(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.browser = HostBrowser(port=9333, state_dir=Path(self.tmp_dir.name), headless=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_start_reuses_responding_browser(self):
        with patch.object(HostBrowser, "is_responding", return_value=True), patch("subprocess.Popen") as mock_popen:
            self.browser.start()
        mock_popen.assert_not_called()
        self.assertFalse(self.browser.pid_file.exists())

    def test_start_fails_when_port_taken_by_something_else(self):
        with patch.object(HostBrowser, "is_responding", return_value=False), \
             patch.object(HostBrowser, "is_port_in_use", return_value=True):
            with self.assertRaisesRegex(HostBrowserError, "in use by something other than Chrome"):
                self.browser.start()

    def test_start_fails_without_chrome(self):
        with patch.object(HostBrowser, "is_responding", return_value=False), \
             patch.object(HostBrowser, "is_port_in_use", return_value=False), \
             patch("agy_sandbox.host_browser.find_chrome_binary", return_value=None):
            with self.assertRaisesRegex(HostBrowserError, "not found on the host"):
                self.browser.start()

    def test_start_launches_chrome_with_dedicated_profile(self):
        proc = MagicMock(pid=4242)
        proc.poll.return_value = None
        with patch.object(HostBrowser, "is_responding", side_effect=[False, True]), \
             patch.object(HostBrowser, "is_port_in_use", return_value=False), \
             patch("agy_sandbox.host_browser.find_chrome_binary", return_value="/opt/chrome"), \
             patch("subprocess.Popen", return_value=proc) as mock_popen:
            self.browser.start()

        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd[0], "/opt/chrome")
        self.assertIn("--remote-debugging-port=9333", cmd)
        self.assertIn(f"--user-data-dir={self.browser.profile_dir}", cmd)
        self.assertIn("--headless=new", cmd)
        self.assertTrue(mock_popen.call_args[1]["start_new_session"])
        self.assertEqual(self.browser.pid_file.read_text(), "4242")

    def test_start_reports_early_exit(self):
        proc = MagicMock(pid=1, returncode=3)
        proc.poll.return_value = 3
        with patch.object(HostBrowser, "is_responding", return_value=False), \
             patch.object(HostBrowser, "is_port_in_use", return_value=False), \
             patch("agy_sandbox.host_browser.find_chrome_binary", return_value="/opt/chrome"), \
             patch("subprocess.Popen", return_value=proc):
            with self.assertRaisesRegex(HostBrowserError, "exited during startup"):
                self.browser.start()

    def test_stop_without_pid_file_is_noop(self):
        with patch("os.kill") as mock_kill:
            self.browser.stop()
        mock_kill.assert_not_called()

    def test_stop_kills_browser_it_started(self):
        self.browser.pid_file.write_text("4242")
        with patch.object(HostBrowser, "is_responding", return_value=True), patch("os.kill") as mock_kill:
            self.browser.stop()
        mock_kill.assert_called_once_with(4242, signal.SIGTERM)
        self.assertFalse(self.browser.pid_file.exists())

    def test_stop_ignores_stale_pid_file(self):
        self.browser.pid_file.write_text("4242")
        with patch.object(HostBrowser, "is_responding", return_value=False), patch("os.kill") as mock_kill:
            self.browser.stop()
        mock_kill.assert_not_called()
        self.assertFalse(self.browser.pid_file.exists())


class TestHostBrowserSandboxWiring(unittest.TestCase):
    def test_validate_requires_sbx(self):
        config = sbx_config(host_browser=True)
        config.sbx.enabled = False
        with self.assertRaisesRegex(HostBrowserError, "requires Docker Sandboxes"):
            validate_host_browser(config)

    def test_validate_requires_chrome_devtools_kit(self):
        with self.assertRaisesRegex(HostBrowserError, f"kits add {KIT_CHROME_DEVTOOLS}"):
            validate_host_browser(sbx_config(kits=[], host_browser=True))

    def test_kit_args_carry_host_port(self):
        self.assertEqual(host_browser_kit_args(sbx_config()), [f"{KIT_CHROME_HOST}.host_port=9333"])

    def test_resolve_run_kits_appends_extra_kits(self):
        resolved = resolve_run_kits(sbx_config(), extra_kits=[KIT_CHROME_HOST])
        self.assertTrue(resolved[0].endswith(os.path.join("kits", "claude")))
        self.assertTrue(resolved[-1].endswith(os.path.join("kits", KIT_CHROME_HOST)))

    def test_resolve_run_kits_uses_agy_mixin_next_to_other_primary(self):
        resolved = resolve_run_kits(sbx_config(kits=["agy", KIT_CHROME_DEVTOOLS]))
        self.assertIn(AGY_MIXIN_KIT_URL, resolved)
        self.assertNotIn(DEFAULT_SBX_KIT_URL, resolved)

    def test_resolve_run_kits_passes_agy_workload_as_agent_not_kit(self):
        config = sbx_config(agent="agy", kits=[KIT_CHROME_DEVTOOLS])
        resolved = resolve_run_kits(config)
        self.assertEqual(config.agent_spec.sbx_agent_arg, DEFAULT_SBX_KIT_URL)
        self.assertNotIn(DEFAULT_SBX_KIT_URL, resolved)
        self.assertNotIn(AGY_MIXIN_KIT_URL, resolved)

    def test_run_up_new_sandbox_starts_browser_and_adds_kit(self):
        config = sbx_config(host_browser=True)
        with patch("agy_sandbox.engine.SbxRunner.check_sbx_availability"), \
             patch("agy_sandbox.engine.sandbox_exists", return_value=False), \
             patch("agy_sandbox.engine.build_project_image", return_value="img"), \
             patch.object(HostBrowser, "start") as mock_start, \
             patch("agy_sandbox.engine.run_up_sbx") as mock_run:
            run_up(config)
        mock_start.assert_called_once()
        kwargs = mock_run.call_args[1]
        self.assertEqual(kwargs["extra_kits"], [KIT_CHROME_HOST])
        self.assertEqual(kwargs["kit_args"], [f"{KIT_CHROME_HOST}.host_port=9333"])

    def test_run_up_without_switch_leaves_browser_alone(self):
        with patch("agy_sandbox.engine.SbxRunner.check_sbx_availability"), \
             patch("agy_sandbox.engine.sandbox_exists", return_value=False), \
             patch("agy_sandbox.engine.build_project_image", return_value="img"), \
             patch.object(HostBrowser, "start") as mock_start, \
             patch("agy_sandbox.engine.run_up_sbx") as mock_run:
            run_up(sbx_config())
        mock_start.assert_not_called()
        self.assertEqual(mock_run.call_args[1]["extra_kits"], [])

    def test_existing_sandbox_without_host_kit_requires_rebuild(self):
        with patch("agy_sandbox.engine.sandbox_has_env", return_value=False):
            with self.assertRaisesRegex(HostBrowserError, "--host-browser --rebuild"):
                check_existing_sandbox_host_browser(sbx_config(host_browser=True))

    def test_existing_sandbox_with_host_kit_is_accepted(self):
        with patch("agy_sandbox.engine.sandbox_has_env", return_value=True):
            check_existing_sandbox_host_browser(sbx_config(host_browser=True))

    def test_existing_host_sandbox_without_switch_only_warns(self):
        with patch("agy_sandbox.engine.sandbox_has_env", return_value=True), patch("builtins.print") as mock_print:
            check_existing_sandbox_host_browser(sbx_config())
        self.assertIn("--host-browser", mock_print.call_args[0][0])


class TestChromeHostRelay(unittest.TestCase):
    def setUp(self):
        self.relay = load_relay_module()

    def test_rewrites_plain_request_into_proxy_form(self):
        head = b"GET /json/version HTTP/1.1\r\nHost: 127.0.0.1:9222\r\nConnection: keep-alive\r\nAccept: */*\r\n\r\n"
        out = self.relay.rewrite_request_head(head, "host.docker.internal:9333", "127.0.0.1:9222")
        self.assertEqual(
            out,
            b"GET http://host.docker.internal:9333/json/version HTTP/1.1\r\n"
            b"Host: 127.0.0.1:9222\r\nAccept: */*\r\nConnection: close\r\n\r\n",
        )

    def test_keeps_websocket_upgrade_headers(self):
        head = (
            b"GET /devtools/browser/abc HTTP/1.1\r\nHost: localhost:9222\r\n"
            b"Connection: Upgrade\r\nUpgrade: websocket\r\n\r\n"
        )
        out = self.relay.rewrite_request_head(head, "host.docker.internal:9333", "127.0.0.1:9222")
        self.assertTrue(out.startswith(b"GET http://host.docker.internal:9333/devtools/browser/abc HTTP/1.1\r\n"))
        self.assertIn(b"Connection: Upgrade\r\n", out)
        self.assertIn(b"Upgrade: websocket\r\n", out)
        self.assertNotIn(b"localhost", out)

    def test_requires_proxy_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                self.relay.proxy_address()


if __name__ == "__main__":
    unittest.main()
