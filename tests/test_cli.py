import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from agy_sandbox.cli import main
from agy_sandbox.cli_helpers import find_best_match, preprocess_unquoted_comma_args
from agy_sandbox.config import load_config


class InTempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()


class TestInitAndAdd(InTempDir):
    def test_default_init(self):
        self.assertEqual(main(["init"]), 0)
        config = load_config("agy.yaml")
        self.assertEqual(config.agent, "agy")
        self.assertEqual(config.kits, ["agy"])
        self.assertEqual(config.auth_mode, "sandbox-local")
        self.assertNotIn("sbx", yaml.safe_load(Path("agy.yaml").read_text()))

    def test_multi_agent_init_with_kit_and_dockerfile(self):
        ret = main(["init", "--agent", "claude,opencode", "--clone", "--auth", "proxy-managed",
                    "--with-kit", "chrome-devtools", "--dockerfile"])
        self.assertEqual(ret, 0)
        config = load_config("agy.yaml")
        self.assertEqual(config.agent, "claude")
        self.assertEqual(config.kits, ["claude", "opencode", "chrome-devtools"])
        self.assertTrue(config.clone)
        self.assertEqual(config.auth_mode, "proxy-managed")
        self.assertTrue(os.path.exists("Dockerfile.agy"))

    def test_prime_agent_init(self):
        self.assertEqual(main(["init", "--agent", "prime-agent"]), 0)
        config = load_config("agy.yaml")
        self.assertEqual(config.agent, "prime-agent")
        self.assertEqual(config.kits, ["prime-agent"])

    def test_agents_add_and_kits_add_are_the_same(self):
        main(["init", "--agent", "claude"])
        self.assertEqual(main(["agents", "add", "codex"]), 0)
        self.assertEqual(main(["kits", "add", "omp"]), 0)
        self.assertEqual(main(["kits", "add", "omp"]), 0)
        config = load_config("agy.yaml")
        self.assertEqual(config.agent, "claude")
        self.assertEqual(config.kits, ["claude", "codex", "omp"])

    def test_add_shell_sets_primary_only(self):
        main(["init", "--agent", "claude"])
        self.assertEqual(main(["agents", "add", "shell"]), 0)
        config = load_config("agy.yaml")
        self.assertEqual(config.agent, "shell")
        self.assertEqual(config.kits, ["claude"])

    def test_add_without_config(self):
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(main(["kits", "add", "omp"]), 1)
        self.assertIn("not found", err.getvalue())

    def test_listings(self):
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(main(["agents", "list"]), 0)
            self.assertEqual(main(["kits", "list"]), 0)
        for name in ("agy", "claude", "shell", "chrome-devtools", "mojo-stdlib", "agy-proxy"):
            self.assertIn(name, out.getvalue())


class TestConfigMigrate(InTempDir):
    LEGACY = ("profile: default\nproject_name: old\nagent: claude\nauth_mode: sbx_proxy\nuse_native_login: false\n"
              "sbx:\n  enabled: true\n  agent: claude\n  clone: true\n  kits: [claude, omp]\n")

    def test_commands_warn_on_legacy_layout(self):
        Path("agy.yaml").write_text(self.LEGACY)
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(main(["kits", "add", "prime-agent"]), 0)
        self.assertIn("pre-0.5 layout", err.getvalue())
        self.assertIn("agy-sandbox config migrate", err.getvalue())

    def test_migrate_rewrites_file(self):
        Path("agy.yaml").write_text(self.LEGACY)
        self.assertEqual(main(["config", "migrate"]), 0)
        text = Path("agy.yaml").read_text()
        self.assertNotIn("sbx:", text)
        self.assertIn("auth_mode: proxy-managed", text)
        config = load_config("agy.yaml")
        self.assertEqual(config.kits, ["claude", "omp"])
        self.assertTrue(config.clone)
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            main(["config", "migrate"])
        self.assertIn("already in the current layout", out.getvalue())


class TestUpDown(InTempDir):
    def test_up_applies_flags_and_exits_with_session_code(self):
        main(["init", "--agent", "claude", "--with-kit", "chrome-devtools"])
        with patch("agy_sandbox.cli.engine.up", return_value=0) as up:
            self.assertEqual(main(["up", "-p", "3000:3000", "--host-browser"]), 0)
        config, kwargs = up.call_args[0][0], up.call_args[1]
        self.assertEqual(config.ports, ["3000:3000"])
        self.assertTrue(config.host_browser.enabled)
        self.assertFalse(kwargs["rebuild"])
        with patch("agy_sandbox.cli.engine.up", return_value=3), self.assertRaises(SystemExit) as cm:
            main(["up"])
        self.assertEqual(cm.exception.code, 3)

    def test_rebuild_and_down_ask_when_guardian_finds_work(self):
        main(["init", "--agent", "claude"])
        with patch("agy_sandbox.cli.check_for_unsaved_sandbox_work", return_value=True), \
             patch("builtins.input", return_value="n"), patch("agy_sandbox.cli.engine.up") as up, \
             patch("agy_sandbox.cli.engine.down") as down, patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main(["up", "--rebuild"]), 1)
            self.assertEqual(main(["down"]), 1)
        up.assert_not_called()
        down.assert_not_called()
        with patch("agy_sandbox.cli.check_for_unsaved_sandbox_work", return_value=True), \
             patch("builtins.input", return_value="y"), patch("agy_sandbox.cli.engine.down") as down:
            self.assertEqual(main(["down"]), 0)
        down.assert_called_once()

    def test_sandbox_errors_are_reported_not_raised(self):
        from agy_sandbox.sandbox import SandboxError
        main(["init"])
        with patch("agy_sandbox.cli.engine.up", side_effect=SandboxError("sandboxd is down")), \
             patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(main(["up"]), 1)
        self.assertIn("sandboxd is down", err.getvalue())

    def test_update_base_rebuilds_without_cache(self):
        with patch("agy_sandbox.cli.images.build_base_image") as base, patch("agy_sandbox.cli.images.build_agent_layers") as layers:
            self.assertEqual(main(["update-base"]), 0)
        base.assert_called_once_with(no_cache=True)
        layers.assert_not_called()
        main(["init", "--agent", "claude"])
        with patch("agy_sandbox.cli.images.build_base_image"), patch("agy_sandbox.cli.images.build_agent_layers") as layers:
            main(["update-base"])
        self.assertEqual(layers.call_args[0][0].agent, "claude")
        self.assertTrue(layers.call_args[1]["no_cache"])


class TestSmartErrors(unittest.TestCase):
    def test_preprocess_unquoted_comma_args(self):
        self.assertEqual(
            preprocess_unquoted_comma_args(["auto-init", "--agent", "omp,", "prime-agent"]),
            ["auto-init", "--agent", "omp,prime-agent"],
        )
        self.assertEqual(
            preprocess_unquoted_comma_args(["auto-init", "--agent", "opencode,", "omp,", "prime-agent"]),
            ["auto-init", "--agent", "opencode,omp,prime-agent"],
        )

    def test_find_best_match(self):
        choices = ["agy", "claude", "opencode", "codex", "omp", "prime-agent"]
        self.assertEqual(find_best_match("claud", choices), "claude")
        self.assertIsNone(find_best_match("completely_unrelated_string_12345", choices))

    def test_unknown_command(self):
        with patch("sys.stderr", new_callable=io.StringIO) as err, self.assertRaises(SystemExit) as cm:
            main(["autoinit"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("Did you mean 'auto-init'?", err.getvalue())

    def test_removed_sbx_flag_is_rejected(self):
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as cm:
            main(["init", "--sbx"])
        self.assertEqual(cm.exception.code, 2)

    def test_unknown_add_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                main(["init"])
                for argv, hint in ((["agents", "add", "claud"], "claude"), (["kits", "add", "devtools"], "chrome-devtools")):
                    with patch("sys.stderr", new_callable=io.StringIO) as err:
                        self.assertEqual(main(argv), 1)
                    self.assertIn(f"Did you mean '{hint}'?", err.getvalue())
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
