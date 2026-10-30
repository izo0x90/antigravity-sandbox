import os
import tempfile
import unittest
from pathlib import Path

from agy_sandbox.config import AgyConfig, HostBrowserConfig, default_config, load_config, migration_warning, save_config


class TestFlatConfig(unittest.TestCase):
    def test_defaults(self):
        config = AgyConfig.from_dict({})
        self.assertEqual(config.agent, "agy")
        self.assertEqual(config.auth_mode, "sandbox-local")
        self.assertFalse(config.clone)
        self.assertEqual(config.kits, [])
        self.assertEqual(config.migration_notes, [])

    def test_null_values_are_tolerated(self):
        config = AgyConfig.from_dict({
            "profile": None, "project_name": None, "agent": None, "kits": None,
            "build_args": None, "setup_scripts": None, "env": None, "ports": None,
        })
        self.assertEqual(config.profile, "default")
        self.assertEqual(config.project_name, "default_project")
        self.assertEqual(config.build_args, {})

    def test_names(self):
        config = AgyConfig(project_name="My App", profile="client_a")
        self.assertEqual(config.sandbox_name, "agy-sandbox-my-app--client-a")
        self.assertEqual(AgyConfig(project_name="my_app").sandbox_name, "agy-sandbox-my-app")
        self.assertEqual(config.image_name, "agy-sandbox-my-app")

    def test_all_kits_puts_primary_agent_first(self):
        config = AgyConfig(agent="claude", kits=["chrome-devtools", "claude", "opencode", "."])
        self.assertEqual(config.all_kits(), ["claude", "chrome-devtools", "opencode"])

    def test_shell_agent_has_no_kit(self):
        self.assertEqual(AgyConfig(agent="shell", kits=["omp"]).all_kits(), ["omp"])

    def test_requested_agent_specs_from_names_and_paths(self):
        config = AgyConfig(agent="agy", kits=["/usr/share/kits/omp", "/opt/kits/prime-agent", "chrome-devtools"])
        self.assertEqual([s.identifier for s in config.requested_agent_specs()], ["agy", "omp", "prime-agent"])

    def test_invalid_auth_mode_and_agent_are_rejected(self):
        with self.assertRaises(ValueError):
            AgyConfig.from_dict({"auth_mode": "magic"})
        with self.assertRaises(ValueError):
            AgyConfig.from_dict({"agent": "nope"})

    def test_to_dict_omits_unset_optional_sections(self):
        data = AgyConfig(agent="claude", kits=["claude"]).to_dict()
        self.assertNotIn("ports", data)
        self.assertNotIn("host_browser", data)
        self.assertNotIn("kit_args", data)
        data = AgyConfig(ports=["3000:3000"], kit_args={"agy-proxy.auth_method": "gcp"}).to_dict()
        self.assertEqual(data["ports"], ["3000:3000"])
        self.assertEqual(data["kit_args"], {"agy-proxy.auth_method": "gcp"})

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agy.yaml")
            config = AgyConfig(
                project_name="demo", agent="claude", kits=["claude", "opencode"], auth_mode="proxy-managed",
                clone=True, ports=["8080:80"], host_browser=HostBrowserConfig(enabled=True, port=9333, headless=True),
                build_args={"PYTHON_VERSION": "3.12"}, setup_scripts=["uv sync"], env=["A=1"],
            )
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded, config)
            self.assertNotIn("sbx:", Path(path).read_text())

    def test_default_config(self):
        config = default_config(agent="claude", kits=("opencode", "chrome-devtools", "claude"), clone=True, project_name="x")
        self.assertEqual(config.agent, "claude")
        self.assertEqual(config.kits, ["claude", "opencode", "chrome-devtools"])
        self.assertTrue(config.clone)
        self.assertEqual(config.env, ["ENVIRONMENT=development"])
        self.assertEqual(config.build_args, {})


class TestLegacyMigration(unittest.TestCase):
    LEGACY = {
        "profile": "default",
        "project_name": "old_app",
        "agent": "claude",
        "auth_mode": "sbx_proxy",
        "use_native_login": False,
        "workspace_path": "/workspace",
        "sbx": {"enabled": True, "agent": "claude", "clone": True, "kits": ["claude", "omp"], "ports": ["3000:3000"]},
    }

    def test_legacy_file_loads_with_notes(self):
        config = AgyConfig.from_dict(self.LEGACY)
        self.assertEqual(config.agent, "claude")
        self.assertEqual(config.kits, ["claude", "omp"])
        self.assertTrue(config.clone)
        self.assertEqual(config.ports, ["3000:3000"])
        self.assertEqual(config.auth_mode, "proxy-managed")
        notes = "\n".join(config.migration_notes)
        for fragment in ("`sbx:` block", "use_native_login", "workspace_path", "sbx_proxy"):
            self.assertIn(fragment, notes)

    def test_sbx_persistent_maps_to_sandbox_local(self):
        self.assertEqual(AgyConfig.from_dict({"auth_mode": "sbx_persistent"}).auth_mode, "sandbox-local")

    def test_sbx_boolean_and_runtime_block(self):
        config = AgyConfig.from_dict({"sbx": True, "runtime": {"python": "3.11", "apt_packages": ["cmake", "ffmpeg"]}})
        self.assertEqual(config.build_args, {"PYTHON_VERSION": "3.11", "APT_PACKAGES": "cmake ffmpeg"})
        self.assertTrue(any("runtime" in n for n in config.migration_notes))

    def test_migration_warning_text(self):
        config = AgyConfig.from_dict(self.LEGACY)
        warning = migration_warning(config, "agy.yaml")
        self.assertIn("pre-0.5 layout", warning)
        self.assertIn("agy-sandbox config migrate", warning)
        self.assertIsNone(migration_warning(AgyConfig()))

    def test_saving_a_migrated_config_writes_the_new_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agy.yaml")
            save_config(AgyConfig.from_dict(self.LEGACY), path)
            text = Path(path).read_text()
            self.assertNotIn("sbx:", text)
            self.assertNotIn("use_native_login", text)
            self.assertIn("auth_mode: proxy-managed", text)
            self.assertEqual(load_config(path).migration_notes, [])


if __name__ == "__main__":
    unittest.main()
