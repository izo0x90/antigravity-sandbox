import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from agy_sandbox.cli import main
from agy_sandbox.cli_helpers import find_best_match, preprocess_unquoted_comma_args
from agy_sandbox.config import load_config
from agy_sandbox.errors import SandboxError, UpgradeRefused

from tests.support import captured_console, messages_containing


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
        with captured_console():
            self.assertEqual(main(["init"]), 0)
        config = load_config("agy.yaml")
        self.assertEqual(config.agent, "agy")
        self.assertEqual(config.kits, ["agy"])
        self.assertEqual(config.auth_mode, "sandbox-local")
        self.assertNotIn("sbx", yaml.safe_load(Path("agy.yaml").read_text()))

    def test_multi_agent_init_with_kit_and_dockerfile(self):
        with captured_console():
            ret = main(["init", "--agent", "claude,opencode", "--clone", "--auth", "proxy-managed",
                        "--with-kit", "chrome-devtools", "--dockerfile"])
        self.assertEqual(ret, 0)
        config = load_config("agy.yaml")
        self.assertEqual(config.agent, "claude")
        self.assertEqual(config.kits, ["claude", "opencode", "chrome-devtools"])
        self.assertTrue(config.clone)
        self.assertEqual(config.auth_mode, "proxy-managed")
        self.assertIn("docker.io/sbx/shell-image", Path("Dockerfile.agy").read_text())

    def test_agents_add_and_kits_add_are_the_same(self):
        with captured_console():
            main(["init", "--agent", "claude"])
            self.assertEqual(main(["agents", "add", "codex"]), 0)
            self.assertEqual(main(["kits", "add", "omp"]), 0)
            self.assertEqual(main(["kits", "add", "omp"]), 0)
        config = load_config("agy.yaml")
        self.assertEqual(config.agent, "claude")
        self.assertEqual(config.kits, ["claude", "codex", "omp"])

    def test_add_shell_sets_primary_only(self):
        with captured_console():
            main(["init", "--agent", "claude"])
            self.assertEqual(main(["agents", "add", "shell"]), 0)
        config = load_config("agy.yaml")
        self.assertEqual(config.agent, "shell")
        self.assertEqual(config.kits, ["claude"])

    def test_credential_kits_cannot_be_added_by_hand(self):
        with captured_console():
            main(["init", "--agent", "claude"])
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(main(["kits", "add", "claude-credentials"]), 1)
        self.assertIn("Unknown kit", err.getvalue())

    def test_add_without_config_reports_the_hint(self):
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(main(["kits", "add", "omp"]), 1)
        self.assertIn("not found", err.getvalue())
        self.assertIn("💡", err.getvalue())

    def test_listings_hide_credential_kits(self):
        with captured_console() as out:
            self.assertEqual(main(["agents", "list"]), 0)
            self.assertEqual(main(["kits", "list"]), 0)
        text = "\n".join(m for _, m in out)
        for name in ("agy", "claude", "shell", "chrome-devtools", "mojo-stdlib", "chrome-host"):
            self.assertIn(name, text)
        self.assertFalse(re.search(r"^\s*\* \S+-credentials", text, re.MULTILINE), text)


class TestConfigMigrate(InTempDir):
    LEGACY = ("profile: default\nproject_name: old\nagent: claude\nauth_mode: sbx_proxy\nuse_native_login: false\n"
              "sbx:\n  enabled: true\n  agent: claude\n  clone: true\n  kits: [claude, omp]\n")

    def test_commands_warn_on_legacy_layout(self):
        Path("agy.yaml").write_text(self.LEGACY)
        with captured_console() as out:
            self.assertEqual(main(["kits", "add", "prime-agent"]), 0)
        warnings = messages_containing(out, "pre-0.5 layout")
        self.assertEqual(len(warnings), 1)
        self.assertIn("agy-sandbox config migrate", warnings[0])

    def test_migrate_rewrites_file(self):
        Path("agy.yaml").write_text(self.LEGACY)
        with captured_console() as out:
            self.assertEqual(main(["config", "migrate"]), 0)
            text = Path("agy.yaml").read_text()
            self.assertNotIn("sbx:", text)
            self.assertIn("auth_mode: proxy-managed", text)
            config = load_config("agy.yaml")
            self.assertEqual(config.kits, ["claude", "omp"])
            self.assertTrue(config.clone)
            main(["config", "migrate"])
        self.assertTrue(messages_containing(out, "already in the current layout"))


class TestLifecycleCommands(InTempDir):
    def setUp(self):
        super().setUp()
        with captured_console():
            main(["init", "--agent", "claude", "--with-kit", "chrome-devtools"])

    def test_up_applies_flags_and_exits_with_session_code(self):
        with patch("agy_sandbox.cli.engine.up", return_value=0) as up:
            self.assertEqual(main(["up", "-p", "3000:3000", "--host-browser"]), 0)
        config, kwargs = up.call_args[0][0], up.call_args[1]
        self.assertEqual(config.ports, ["3000:3000"])
        self.assertTrue(config.host_browser.enabled)
        self.assertFalse(kwargs["rebuild"])
        self.assertEqual(kwargs["cwd"], Path.cwd())
        with patch("agy_sandbox.cli.engine.up", return_value=3), self.assertRaises(SystemExit) as cm:
            main(["up"])
        self.assertEqual(cm.exception.code, 3)

    def test_upgrade_and_recreate_apply_flags(self):
        with patch("agy_sandbox.cli.upgrade.upgrade", return_value=0) as up:
            self.assertEqual(main(["upgrade", "-p", "8080:80", "--host-browser"]), 0)
        self.assertEqual(up.call_args[0][0].ports, ["8080:80"])
        self.assertTrue(up.call_args[0][0].host_browser.enabled)
        with patch("agy_sandbox.cli.upgrade.recreate", return_value=0) as rc, \
             patch("agy_sandbox.cli.check_for_unsaved_sandbox_work", return_value=False) as guard:
            self.assertEqual(main(["recreate"]), 0)
        rc.assert_called_once()
        guard.assert_called_once()

    def test_upgrade_refusal_lists_reasons_and_hint(self):
        with patch("agy_sandbox.cli.upgrade.upgrade", side_effect=UpgradeRefused(["kit removed: omp"], hint="Run 'agy-sandbox recreate'")), \
             patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(main(["upgrade"]), 1)
        self.assertIn("kit removed: omp", err.getvalue())
        self.assertIn("💡 Run 'agy-sandbox recreate'", err.getvalue())

    def test_rebuild_and_down_ask_when_guardian_finds_work(self):
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

    def test_yes_skips_the_prompt_and_no_attach_reaches_the_engine(self):
        with patch("agy_sandbox.cli.check_for_unsaved_sandbox_work", return_value=True), \
             patch("builtins.input", side_effect=AssertionError("must not prompt")), \
             patch("agy_sandbox.cli.engine.down") as down, patch("agy_sandbox.cli.engine.up", return_value=0) as up, \
             patch("agy_sandbox.cli.upgrade.recreate", return_value=0) as rc, patch("agy_sandbox.cli.upgrade.upgrade", return_value=0) as ug, \
             patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main(["down", "-y"]), 0)
            self.assertEqual(main(["up", "--rebuild", "--yes", "--no-attach"]), 0)
            self.assertEqual(main(["recreate", "-y", "--no-attach"]), 0)
            self.assertEqual(main(["upgrade", "--no-attach"]), 0)
        down.assert_called_once()
        self.assertFalse(up.call_args.kwargs["attach"])
        self.assertFalse(rc.call_args.kwargs["attach"])
        self.assertFalse(ug.call_args.kwargs["attach"])
        with patch("agy_sandbox.cli.engine.up", return_value=0) as up:
            self.assertEqual(main(["up"]), 0)
        self.assertTrue(up.call_args.kwargs["attach"])

    def test_sandbox_errors_are_rendered_with_hint_not_raised(self):
        with patch("agy_sandbox.cli.engine.up", side_effect=SandboxError("sandboxd is down", hint="sbx daemon start")), \
             patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(main(["up"]), 1)
        self.assertIn("❌ sandboxd is down", err.getvalue())
        self.assertIn("💡 sbx daemon start", err.getvalue())

    def test_update_base_is_gone(self):
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as cm:
            main(["update-base"])
        self.assertEqual(cm.exception.code, 2)


class TestSmartErrors(unittest.TestCase):
    def test_preprocess_unquoted_comma_args(self):
        self.assertEqual(
            preprocess_unquoted_comma_args(["auto-init", "--agent", "omp,", "prime-agent"]),
            ["auto-init", "--agent", "omp,prime-agent"],
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

    def test_unknown_add_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with captured_console():
                    main(["init"])
                for argv, hint in ((["agents", "add", "claud"], "claude"), (["kits", "add", "devtools"], "chrome-devtools")):
                    with patch("sys.stderr", new_callable=io.StringIO) as err:
                        self.assertEqual(main(argv), 1)
                    self.assertIn(f"Did you mean '{hint}'?", err.getvalue())
            finally:
                os.chdir(cwd)
