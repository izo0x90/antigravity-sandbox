import io
import sys
import unittest
from unittest.mock import MagicMock, call, patch

from agy_sandbox.config import AgyConfig, SbxConfig
from agy_sandbox.engine import run_up
from agy_sandbox.runners.sbx import SbxRunner, run_sbx_ls, sandbox_exists


class TestSbxPreflightAndResume(unittest.TestCase):
    def test_check_sbx_availability_missing_binary(self):
        with patch("shutil.which", return_value=None), \
             patch("sys.exit") as mock_exit, \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            SbxRunner.check_sbx_availability("agy")
            mock_exit.assert_called_once_with(1)
            self.assertIn("Docker Sandboxes CLI ('sbx') is not installed", mock_stderr.getvalue())

    def test_check_sbx_availability_daemon_stopped(self):
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_res.stdout = "Status: stopped\n"
        mock_res.stderr = ""
        with patch("shutil.which", return_value="/usr/local/bin/sbx"), \
             patch("subprocess.run", return_value=mock_res) as mock_run, \
             patch("sys.exit") as mock_exit, \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            SbxRunner.check_sbx_availability("omp")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertEqual(cmd, ["sbx", "daemon", "status"])
            mock_exit.assert_called_once_with(1)
            self.assertIn("Docker Sandboxes daemon ('sandboxd') is not running", mock_stderr.getvalue())

    def test_check_sbx_availability_daemon_running(self):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "Status: running\nSocket: /tmp/sandboxd.sock\n"
        mock_res.stderr = ""
        with patch("shutil.which", return_value="/usr/local/bin/sbx"), \
             patch("subprocess.run", return_value=mock_res) as mock_run:
            # Should succeed cleanly without calling sys.exit
            SbxRunner.check_sbx_availability("omp")
            mock_run.assert_called_once()
            self.assertEqual(mock_run.call_args[0][0], ["sbx", "daemon", "status"])

    def test_run_sbx_ls_streams_auth_messages_live(self):
        lines = [
            "You are not authenticated to Docker. Starting the sign-in flow...\n",
            "Your one-time device confirmation code is: CGNF-WDFF\n",
            "Open this URL to sign in: https://login.docker.com/activate?user_code=CGNF-WDFF\n",
            "Waiting for authentication...\n",
            "Signed in as testuser.\n",
            "SANDBOX                      AGENT   STATUS    PORTS   WORKSPACE\n",
            "agy-sandbox-hexing-unit      shell   stopped           /Users/test/hexing_unit\n",
        ]
        mock_proc = MagicMock()
        mock_proc.stdout.readline.side_effect = lines + [""]
        mock_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            output = run_sbx_ls()
            # Verify auth lines were streamed to terminal in real time
            terminal_output = mock_stdout.getvalue()
            self.assertIn("Your one-time device confirmation code is: CGNF-WDFF", terminal_output)
            self.assertIn("Signed in as testuser.", terminal_output)
            # Verify captured output contains table
            self.assertIn("agy-sandbox-hexing-unit", output)

    def test_run_sbx_ls_raises_runtime_error_on_failure(self):
        mock_proc = MagicMock()
        mock_proc.stdout.readline.side_effect = ["Error connecting to daemon\n", ""]
        mock_proc.wait.return_value = 1

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(RuntimeError) as cm:
                run_sbx_ls()
            self.assertIn("Failed to query Docker Sandboxes via 'sbx ls'", str(cm.exception))

    def test_sandbox_exists_returns_true_for_matching_row(self):
        sample_output = (
            "SANDBOX                      AGENT   STATUS    PORTS   WORKSPACE\n"
            "agy-sandbox-enterprise-web   shell   stopped           /Users/izo/code/enterprise-web\n"
            "agy-sandbox-hexing-unit      shell   stopped           /Users/izo/code/hexing_unit\n"
        )
        with patch("agy_sandbox.runners.sbx.run_sbx_ls", return_value=sample_output):
            self.assertTrue(sandbox_exists("agy-sandbox-hexing-unit"))
            self.assertFalse(sandbox_exists("agy-sandbox-unknown"))

    def test_sandbox_exists_propagates_runtime_error(self):
        with patch("agy_sandbox.runners.sbx.run_sbx_ls", side_effect=RuntimeError("Auth timeout")):
            with self.assertRaises(RuntimeError):
                sandbox_exists("agy-sandbox-hexing-unit")

    def test_run_up_preflight_and_resume_order(self):
        config = AgyConfig(
            project_name="hexing-unit",
            agent="omp",
            sbx=SbxConfig(enabled=True, agent="omp", kits=["omp", "prime-agent"]),
        )
        calls = []

        def mock_check_avail(agent):
            calls.append(("check_sbx_availability", agent))

        def mock_exists(name):
            calls.append(("sandbox_exists", name))
            return True

        def mock_run_sbx(cfg, image_name="", rebuild=False):
            calls.append(("run_up_sbx", image_name, rebuild))

        with patch.object(SbxRunner, "check_sbx_availability", side_effect=mock_check_avail), \
             patch("agy_sandbox.engine.sandbox_exists", side_effect=mock_exists), \
             patch("agy_sandbox.engine.build_project_image") as mock_build, \
             patch("agy_sandbox.engine.run_up_sbx", side_effect=mock_run_sbx):
            run_up(config, rebuild=False)

            # 1. Preflight check ran first
            self.assertEqual(calls[0], ("check_sbx_availability", "omp"))
            # 2. Existence check ran second
            self.assertEqual(calls[1], ("sandbox_exists", config.sandbox_name))
            # 3. Resume ran third with empty image_name
            self.assertEqual(calls[2], ("run_up_sbx", "", False))
            # 4. Zero Docker builds were triggered
            mock_build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
