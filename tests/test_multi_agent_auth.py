import os
import tempfile
import unittest

from agy_sandbox.config import AgyConfig, load_config, write_default_config
from agy_sandbox.constants import (
    AGENT_SPECS,
    DEFAULT_BUILD_ARGS,
    DEFAULT_CMD_TIMEOUT,
    EXIT_CODE_INTERRUPTED,
    REMOVE_CMD_TIMEOUT,
)
from agy_sandbox.kits import validate_and_resolve_kits
from agy_sandbox.naming import SandboxNamingResolver


class TestMultiAgentAuth(unittest.TestCase):
    def test_agent_specs_registry(self):
        self.assertIn("agy", AGENT_SPECS)
        self.assertIn("claude", AGENT_SPECS)
        self.assertIn("opencode", AGENT_SPECS)
        self.assertIn("codex", AGENT_SPECS)
        
        claude_spec = AGENT_SPECS["claude"]
        self.assertEqual(claude_spec.sbx_agent_arg, "claude")
        self.assertEqual(claude_spec.kit_ref, "claude")
        self.assertEqual(claude_spec.profile_dir_name, ".claude")
        self.assertIn(".claude.json", claude_spec.auth_session_files)

        agy_spec = AGENT_SPECS["agy"]
        self.assertEqual(agy_spec.profile_dir_name, ".gemini")
        self.assertIn(".gemini", agy_spec.auth_session_files)

    def test_sandbox_naming_resolver(self):
        self.assertEqual(
            SandboxNamingResolver.resolve_sandbox_name("my_app", "default"),
            "agy-sandbox-my-app",
        )
        self.assertEqual(
            SandboxNamingResolver.resolve_sandbox_name("my_app", "client_a"),
            "agy-sandbox-my-app--client-a",
        )
        self.assertEqual(
            SandboxNamingResolver.resolve_legacy_sandbox_name("my_app"),
            "agy-sandbox-my-app",
        )

    def test_write_default_config_with_agents_and_kits(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = os.path.join(tmp_dir, "agy.yaml")
            write_default_config(
                path=config_path,
                agent="claude",
                additional_agents=["opencode"],
                kits=["chrome-devtools"],
            )

            config = load_config(config_path)
            self.assertEqual(config.agent, "claude")
            self.assertTrue(config.sbx.enabled)
            self.assertIn("claude", config.sbx.kits)
            self.assertIn("opencode", config.sbx.kits)
            self.assertIn("chrome-devtools", config.sbx.kits)

    def test_config_from_dict_null_handling(self):
        null_dict = {
            "profile": None,
            "project_name": None,
            "agent": None,
            "build_args": None,
            "sbx": None,
            "setup_scripts": None,
            "env": None,
        }
        config = AgyConfig.from_dict(null_dict)
        self.assertEqual(config.profile, "default")
        self.assertEqual(config.project_name, "default_project")
        self.assertEqual(config.agent, "agy")
        self.assertEqual(config.build_args, {})
        self.assertEqual(config.setup_scripts, [])
        self.assertEqual(config.env, [])

    def test_config_from_dict_sbx_boolean(self):
        bool_dict = {"sbx": True}
        config = AgyConfig.from_dict(bool_dict)
        self.assertTrue(config.sbx.enabled)
        self.assertEqual(config.agent, "agy")

        false_dict = {"sbx": False}
        config_false = AgyConfig.from_dict(false_dict)
        self.assertFalse(config_false.sbx.enabled)

    def test_resolve_kit_bundled_omp(self):
        from agy_sandbox.kits import resolve_kit
        resolved_omp = resolve_kit("omp")
        self.assertIsNotNone(resolved_omp)
        self.assertTrue(os.path.isdir(resolved_omp))
        self.assertTrue(os.path.exists(os.path.join(resolved_omp, "spec.yaml")))

    def test_validate_and_resolve_kits_fail_fast(self):
        resolved = validate_and_resolve_kits(["claude", "chrome-devtools", "."])
        self.assertIn("claude", resolved)
        self.assertNotIn(".", resolved)

        with self.assertRaises(ValueError):
            validate_and_resolve_kits(["non_existent_invalid_kit_name"])

    def test_constants_and_timeouts(self):
        self.assertEqual(DEFAULT_CMD_TIMEOUT, 10)
        self.assertEqual(REMOVE_CMD_TIMEOUT, 15)
        self.assertEqual(EXIT_CODE_INTERRUPTED, 130)
        self.assertIsInstance(DEFAULT_BUILD_ARGS, dict)


if __name__ == "__main__":
    unittest.main()
