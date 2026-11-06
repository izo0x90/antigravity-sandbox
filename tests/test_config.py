import os
import tempfile
import unittest

import yaml

from agy_sandbox.config import (
    AgyConfig,
    HostBrowserConfig,
    default_config,
    load_config,
    migration_warning,
    sandbox_name,
    save_config,
)
from agy_sandbox.constants import DEFAULT_BASE_IMAGE
from agy_sandbox.errors import ConfigError


class TestNaming(unittest.TestCase):
    def test_sandbox_name_slugifies_and_appends_profile(self):
        self.assertEqual(sandbox_name("My App"), "agy-sandbox-my-app")
        self.assertEqual(sandbox_name("my_app", "staging"), "agy-sandbox-my-app--staging")
        self.assertEqual(AgyConfig(project_name="demo").sandbox_name, "agy-sandbox-demo")


class TestFromDict(unittest.TestCase):
    def test_current_layout_round_trips(self):
        data = {
            "profile": "default", "project_name": "demo", "agent": "claude", "kits": ["claude", "chrome-devtools"],
            "auth_mode": "proxy-managed", "clone": True, "ports": ["3000:3000"],
            "host_browser": {"enabled": True, "port": 9333, "headless": True},
            "kit_args": {"chrome-host.host_port": "9333"}, "base_image": "docker.io/me/base:1",
            "build_args": {"PYTHON_VERSION": "3.12"}, "setup_scripts": ["uv sync"], "env": ["A=1"],
        }
        cfg = AgyConfig.from_dict(data)
        self.assertEqual(cfg.migration_notes, [])
        self.assertEqual(cfg.to_dict(), data)

    def test_defaults_are_minimal_and_base_image_is_omitted_when_default(self):
        cfg = AgyConfig.from_dict({"project_name": "demo"})
        self.assertEqual(cfg.agent, "agy")
        self.assertEqual(cfg.auth_mode, "sandbox-local")
        self.assertEqual(cfg.base_image, DEFAULT_BASE_IMAGE)
        self.assertNotIn("base_image", cfg.to_dict())
        self.assertNotIn("host_browser", cfg.to_dict())
        self.assertNotIn("ports", cfg.to_dict())

    def test_all_kits_puts_primary_agent_first_without_duplicates(self):
        cfg = AgyConfig(agent="claude", kits=["opencode", "claude", ".", ""])
        self.assertEqual(cfg.all_kits(), ["claude", "opencode"])
        self.assertEqual([s.identifier for s in cfg.requested_agent_specs()], ["claude", "opencode"])

    def test_shell_agent_has_no_kit(self):
        cfg = AgyConfig(agent="shell", kits=["chrome-devtools"])
        self.assertEqual(cfg.all_kits(), ["chrome-devtools"])
        self.assertEqual(cfg.requested_agent_specs(), [])

    def test_requested_agents_recognise_local_kit_dirs_by_basename(self):
        cfg = AgyConfig(agent="shell", kits=["/some/where/codex"])
        self.assertEqual([s.identifier for s in cfg.requested_agent_specs()], ["codex"])

    def test_unknown_agent_and_auth_mode_raise_config_error(self):
        with self.assertRaises(ConfigError) as ctx:
            AgyConfig.from_dict({"agent": "nope"})
        self.assertIn("Available agents", ctx.exception.hint)
        with self.assertRaises(ConfigError):
            AgyConfig.from_dict({"auth_mode": "magic"})


class TestLegacyLayout(unittest.TestCase):
    def test_pre_05_file_is_translated_and_noted(self):
        legacy = {
            "project_name": "old", "use_native_login": True, "workspace_path": "/w",
            "runtime": {"python": "3.11", "apt_packages": ["cmake", "jq"]},
            "sbx": {"enabled": True, "agent": "claude", "kits": ["claude"], "clone": True, "auth_mode": "sbx_proxy"},
        }
        cfg = AgyConfig.from_dict(legacy)
        self.assertEqual(cfg.agent, "claude")
        self.assertTrue(cfg.clone)
        self.assertEqual(cfg.auth_mode, "proxy-managed")
        self.assertEqual(cfg.build_args, {"PYTHON_VERSION": "3.11", "APT_PACKAGES": "cmake jq"})
        self.assertEqual([n.split(";")[0].split(" is ")[0] for n in cfg.migration_notes],
                         ["the `sbx:` block", "`use_native_login`", "`workspace_path`", "`runtime:`", "`auth_mode: sbx_proxy`"])
        warning = migration_warning(cfg, "agy.yaml")
        self.assertIn("config migrate", warning)
        self.assertIn("`auth_mode: sbx_proxy` is now `auth_mode: proxy-managed`", warning)
        # Rewriting drops every legacy key
        self.assertEqual(AgyConfig.from_dict(cfg.to_dict()).migration_notes, [])

    def test_current_file_has_no_warning(self):
        self.assertIsNone(migration_warning(AgyConfig()))


class TestFiles(unittest.TestCase):
    def test_load_and_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agy.yaml")
            save_config(AgyConfig(project_name="demo", kits=["agy"], host_browser=HostBrowserConfig(enabled=True)), path)
            cfg = load_config(path)
            self.assertEqual(cfg.project_name, "demo")
            self.assertTrue(cfg.host_browser.enabled)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(list(yaml.safe_load(f))[0], "profile")

    def test_missing_file_is_a_config_error_with_hint(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config("/nonexistent/agy.yaml")
        self.assertIn("auto-init", ctx.exception.hint)

    def test_invalid_yaml_and_non_mapping_are_config_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agy.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("- just\n- a list\n")
            with self.assertRaises(ConfigError):
                load_config(path)
            with open(path, "w", encoding="utf-8") as f:
                f.write("a: [unclosed\n")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_default_config_lists_requested_agents_as_kits(self):
        cfg = default_config(agent="opencode", kits=("codex", "chrome-devtools"), clone=True, project_name="x")
        self.assertEqual(cfg.kits, ["opencode", "codex", "chrome-devtools"])
        self.assertTrue(cfg.clone)
        self.assertEqual(cfg.env, ["ENVIRONMENT=development"])
