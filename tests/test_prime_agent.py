import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agy_sandbox.cli import main
from agy_sandbox.config import load_config
from agy_sandbox.constants import (
    AGENT_PRIME_AGENT,
    AGENT_SPECS,
    discover_available_agent,
)
from agy_sandbox.git_guardian import check_for_unsaved_sandbox_work
from agy_sandbox.spec_inspector import run_read_only_agent_analysis


class TestPrimeAgentIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.old_cwd = os.getcwd()
        os.chdir(self.test_dir)

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_prime_agent_spec_registration(self):
        self.assertIn("prime-agent", AGENT_SPECS)
        spec = AGENT_SPECS["prime-agent"]
        self.assertEqual(spec.identifier, "prime-agent")
        self.assertEqual(spec.cli_binary, "prime-agent")
        self.assertEqual(spec.kit_ref, "prime-agent")
        self.assertIn("prime", spec.sbx_secret_services)
        self.assertIn(".prime", spec.auth_session_files)

    def test_prime_agent_read_only_command(self):
        spec = AGENT_SPECS["prime-agent"]
        cmd = spec.get_read_only_cmd("Analyze this project", self.test_dir)
        self.assertEqual(
            cmd,
            ["prime-agent", "-p", "--no-session", "--no-tools", "Analyze this project"],
        )

    def test_prime_agent_discovery(self):
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda bin_name: "/usr/local/bin/prime-agent" if bin_name == "prime-agent" else None
            disc = discover_available_agent("prime-agent")
            self.assertIsNotNone(disc)
            self.assertEqual(disc.identifier, "prime-agent")

    def test_init_command_with_prime_flag(self):
        exit_code = main(["init", "--agent", "prime-agent"])
        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.exists("agy.yaml"))

        config = load_config("agy.yaml")
        self.assertEqual(config.agent, AGENT_PRIME_AGENT)
        self.assertEqual(config.sbx.agent, AGENT_PRIME_AGENT)
        self.assertTrue(config.sbx.enabled)
        self.assertIn("prime-agent", config.sbx.kits)

    def test_auto_init_command_with_prime_agent(self):
        with patch("agy_sandbox.cli.run_read_only_agent_analysis") as mock_analysis:
            mock_analysis.return_value = {
                "project_name": "prime_test_app",
                "agent": "prime-agent",
                "build_args": {"PYTHON_VERSION": "3.11"},
                "sbx": {"enabled": True, "agent": "prime-agent", "clone": True, "kits": ["prime-agent"]},
            }
            exit_code = main(["auto-init", "--agent", "prime-agent"])
            self.assertEqual(exit_code, 0)
            self.assertTrue(os.path.exists("agy.yaml"))

            config = load_config("agy.yaml")
            self.assertEqual(config.agent, AGENT_PRIME_AGENT)
            self.assertEqual(config.sbx.agent, AGENT_PRIME_AGENT)

    def test_code_guardian_detects_prime_session_files(self):
        mock_config = MagicMock()
        mock_config.sbx.clone = False

        def mock_subprocess_run(cmd, **kwargs):
            res = MagicMock()
            cmd_str = " ".join(cmd)
            if "/home/agent/.prime" in cmd_str:
                res.returncode = 0
            else:
                res.returncode = 1
            return res

        with patch("agy_sandbox.runners.sbx.sandbox_exists", return_value=True):
            with patch("subprocess.run", side_effect=mock_subprocess_run):
                has_unsaved = check_for_unsaved_sandbox_work("test-sandbox", mock_config)
                self.assertTrue(has_unsaved)

    def test_run_read_only_agent_analysis_invokes_prime_agent(self):
        spec = AGENT_SPECS["prime-agent"]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '{"project_name": "mocked_prime", "agent": "prime-agent", "build_args": {"PYTHON_VERSION": "3.11"}}'

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = run_read_only_agent_analysis(spec, "Analyze codebase", self.test_dir)
            self.assertEqual(result["project_name"], "mocked_prime")
            self.assertEqual(result["agent"], "prime-agent")
            mock_run.assert_called_once()
            called_cmd = mock_run.call_args[0][0]
            self.assertEqual(
                called_cmd,
                ["prime-agent", "-p", "--no-session", "--no-tools", "Analyze codebase"],
            )


if __name__ == "__main__":
    unittest.main()
