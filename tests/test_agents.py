import unittest
from unittest.mock import patch

from agy_sandbox.agents import AGENT_SHELL, AGENT_SPECS, DISCOVERY_PRIORITY, discover_available_agent


class TestRegistry(unittest.TestCase):
    def test_every_agent_with_a_kit_has_a_binary_and_read_only_mode(self):
        for identifier, spec in AGENT_SPECS.items():
            self.assertEqual(spec.identifier, identifier)
            if spec.has_kit:
                self.assertTrue(spec.cli_binary, identifier)
                self.assertTrue(spec.read_only_args, identifier)
                self.assertTrue(spec.session_files, identifier)

    def test_shell_is_the_only_agent_without_a_kit(self):
        self.assertEqual([k for k, s in AGENT_SPECS.items() if not s.has_kit], [AGENT_SHELL])

    def test_discovery_priority_covers_every_kit_agent(self):
        self.assertEqual(set(DISCOVERY_PRIORITY), {k for k, s in AGENT_SPECS.items() if s.has_kit})

    def test_read_only_cmd_substitutes_prompt_and_cwd(self):
        cmd = AGENT_SPECS["agy"].read_only_cmd("hello", "/w")
        self.assertEqual(cmd[:3], ["agy", "--add-dir", "/w"])
        self.assertEqual(cmd[-1], "hello")
        self.assertEqual(AGENT_SPECS["shell"].read_only_cmd("x", "/"), [])


class TestDiscovery(unittest.TestCase):
    def test_prefers_requested_agent_when_installed(self):
        with patch("shutil.which", side_effect=lambda b: f"/bin/{b}" if b in ("claude", "agy") else None):
            self.assertEqual(discover_available_agent("claude").identifier, "claude")
            self.assertEqual(discover_available_agent("codex").identifier, "agy")
            self.assertEqual(discover_available_agent(None).identifier, "agy")

    def test_none_when_nothing_installed(self):
        with patch("shutil.which", return_value=None):
            self.assertIsNone(discover_available_agent("claude"))
