import io
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agy_sandbox.cli import main
from agy_sandbox.config import AgyConfig, load_config
from agy_sandbox.constants import AGENT_AGY, AGENT_CLAUDE, AGENT_CODEX, AGENT_OMP, AGENT_OPENCODE, AGENT_SHELL
from agy_sandbox.git_guardian import check_for_unsaved_sandbox_work
from agy_sandbox.runners.docker import DockerRunner
from agy_sandbox.runners.sbx import SbxRunner


class TestE2EFullSuite(unittest.TestCase):
    # =========================================================================
    # Phase 1: CLI Commands & Scaffolding (Isolated Test Workspaces)
    # =========================================================================
    def test_phase1_agents_and_kits_listing(self):
        # Test 'agents list'
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out):
            ret = main(["agents", "list"])
        self.assertEqual(ret, 0)
        output = captured_out.getvalue()
        for agent_id in ["agy", "claude", "opencode", "codex", "omp", "shell"]:
            self.assertIn(agent_id, output)

        # Test 'kits list'
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out):
            ret = main(["kits", "list"])
        self.assertEqual(ret, 0)
        output = captured_out.getvalue()
        for kit_id in ["chrome-devtools", "mojo-stdlib", "omp"]:
            self.assertIn(kit_id, output)

    def test_phase1_init_scaffolding_matrix(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            orig_dir = os.getcwd()
            os.chdir(tmp_dir)
            try:
                # 1. Default init
                ret = main(["init"])
                self.assertEqual(ret, 0)
                config = load_config("agy.yaml")
                self.assertEqual(config.agent, "agy")
                self.assertFalse(config.sbx.enabled)

                # 2. Multi-agent & kit init
                os.remove("agy.yaml")
                ret = main([
                    "init",
                    "--agent", "claude,opencode",
                    "--sbx",
                    "--clone",
                    "--with-kit", "chrome-devtools",
                    "--dockerfile",
                ])
                self.assertEqual(ret, 0)
                config = load_config("agy.yaml")
                self.assertEqual(config.agent, "claude")
                self.assertTrue(config.sbx.enabled)
                self.assertTrue(config.sbx.clone)
                self.assertIn("claude", config.sbx.kits)
                self.assertIn("opencode", config.sbx.kits)
                self.assertIn("chrome-devtools", config.sbx.kits)
                self.assertTrue(os.path.exists("Dockerfile.agy"))

                # 3. agents add
                ret = main(["agents", "add", "codex"])
                self.assertEqual(ret, 0)
                config = load_config("agy.yaml")
                self.assertIn("codex", config.sbx.kits)

                # 4. kits add
                ret = main(["kits", "add", "omp"])
                self.assertEqual(ret, 0)
                config = load_config("agy.yaml")
                self.assertIn("omp", config.sbx.kits)
                self.assertEqual(config.sbx.agent, "omp")
            finally:
                os.chdir(orig_dir)

    # =========================================================================
    # Phase 2: Host Base & Project Image Construction
    # =========================================================================
    def test_phase2_docker_contexts(self):
        df_base, ctx_base = DockerRunner.resolve_docker_context("Dockerfile.base")
        self.assertTrue(df_base.exists())

        df_default, ctx_default = DockerRunner.resolve_docker_context("Dockerfile.default")
        self.assertTrue(df_default.exists())

        df_omp, ctx_omp = DockerRunner.resolve_docker_context("Dockerfile.omp")
        self.assertTrue(df_omp.exists())

    # =========================================================================
    # Phase 3: Sandbox Lifecycle Across All 6 Agent Harnesses
    # =========================================================================
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/sbx")
    def test_phase3_all_6_harnesses_lifecycle(self, mock_which, mock_run):
        harnesses = [
            (AGENT_AGY, "agy", ("google",), "/root/.gemini"),
            (AGENT_CLAUDE, "claude", ("anthropic",), "/home/agent/.claude.json"),
            (AGENT_OPENCODE, "opencode", ("openrouter", "anthropic", "openai", "google"), "/home/agent/.config/opencode/auth.json"),
            (AGENT_CODEX, "codex", ("openai",), "/home/agent/.codex/auth.json"),
            (AGENT_OMP, "shell", (), "/home/agent/.omp"),
            (AGENT_SHELL, "shell", (), "/home/agent/.shell"),
        ]

        for agent_id, expected_arg, expected_secrets, auth_guard_file in harnesses:
            with self.subTest(agent=agent_id):
                mock_run.reset_mock()
                # Mock sbx ls success
                mock_run.return_value = MagicMock(returncode=0, stdout="NAME STATUS\n")

                config = AgyConfig.from_dict({
                    "project_name": "test_project",
                    "agent": agent_id,
                    "auth_mode": "sbx_proxy",
                    "sbx": {
                        "enabled": True,
                        "agent": agent_id,
                        "clone": True,
                        "kits": [agent_id],
                    }
                })

                # Test Preflight & Secret Config
                SbxRunner.check_sbx_availability(config.agent)
                SbxRunner.ensure_secret_configured(config)

                for service in expected_secrets:
                    secret_cmd = ["sbx", "secret", "set", config.sandbox_name, service]
                    mock_run.assert_any_call(secret_cmd, capture_output=True, text=True)

                # Test Run Sandbox Creation
                with patch("agy_sandbox.runners.sbx.sandbox_exists", return_value=False), \
                     patch("agy_sandbox.runners.sbx.get_local_image_id", return_value="123456789012"), \
                     patch("agy_sandbox.runners.sbx.is_image_loaded_in_sbx", return_value=True):
                    
                    SbxRunner.run_sandbox(config, image_name="test-image", rebuild=False)
                    
                    # Verify last run command
                    last_call = mock_run.call_args_list[-1]
                    run_cmd = last_call[0][0]
                    self.assertEqual(run_cmd[0], "sbx")
                    self.assertEqual(run_cmd[1], "run")
                    self.assertIn("--name", run_cmd)
                    self.assertIn(config.sandbox_name, run_cmd)
                    self.assertIn("--clone", run_cmd)
                    self.assertEqual(run_cmd[-1], expected_arg)

                # Test Active Session Guard
                def mock_exec_run(cmd, **kwargs):
                    if cmd[0] == "sbx" and cmd[1] == "ls":
                        return MagicMock(returncode=0, stdout=f"NAME STATUS\n{config.sandbox_name} running\n")
                    path = cmd[-1]
                    if path == auth_guard_file:
                        return MagicMock(returncode=0)
                    return MagicMock(returncode=1)

                mock_run.side_effect = mock_exec_run
                with patch("agy_sandbox.runners.sbx.sandbox_exists", return_value=True):
                    has_unsaved = check_for_unsaved_sandbox_work(config.sandbox_name, config)
                    self.assertTrue(has_unsaved)

                mock_run.side_effect = None

                # Test Stop Sandbox
                with patch("agy_sandbox.runners.sbx.sandbox_exists", side_effect=[True, True, False]):
                    SbxRunner.stop_sandbox(config)
                    mock_run.assert_any_call(["sbx", "stop", config.sandbox_name], capture_output=True, timeout=15)

    # =========================================================================
    # Phase 4: Non-Sandboxed Host Docker Mode (sbx.enabled: false)
    # =========================================================================
    @patch("subprocess.run")
    def test_phase4_host_docker_mode(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        config = AgyConfig.from_dict({
            "project_name": "host_app",
            "profile": "client_b",
            "agent": "agy",
            "use_native_login": False,
            "sbx": {"enabled": False},
            "env": ["FOO=BAR"],
        })

        self.assertEqual(config.container_name, "agy-sandbox-container-host-app--client-b")

        # Run container
        DockerRunner.run_container(config, image_name="host-app-image")
        
        last_call = mock_run.call_args_list[-1]
        run_cmd = last_call[0][0]
        self.assertEqual(run_cmd[0], "docker")
        self.assertEqual(run_cmd[1], "run")
        self.assertIn("agy-sandbox-container-host-app--client-b", run_cmd)
        self.assertIn("-e", run_cmd)
        self.assertIn("FOO=BAR", run_cmd)

        # Stop container
        DockerRunner.stop_container(config)
        mock_run.assert_any_call(["docker", "stop", config.container_name], capture_output=True)
        mock_run.assert_any_call(["docker", "rm", config.container_name], capture_output=True)


if __name__ == "__main__":
    unittest.main()
