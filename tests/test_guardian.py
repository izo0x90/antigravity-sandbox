import subprocess
import unittest
from unittest.mock import patch

from agy_sandbox import guardian
from agy_sandbox.config import AgyConfig
from agy_sandbox.sandbox import SandboxError


def completed(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


class TestGuardian(unittest.TestCase):
    def test_script_covers_every_agent_and_git_only_for_clones(self):
        script = guardian.scan_script(check_git=False)
        for path in ("/home/agent/.gemini", "/home/agent/.claude.json", "/home/agent/.codex/auth.json", "/home/agent/.prime"):
            self.assertIn(path, script)
        self.assertNotIn("git status", script)
        self.assertIn("git status --porcelain", guardian.scan_script(check_git=True))

    def test_scan_parses_tagged_output(self):
        out = "SESSION /home/agent/.prime\nDIRTY  M src/x.py\nUNPUSHED + abc fix\nBRANCH feature wip\n"
        with patch("agy_sandbox.sandbox.exec_script", return_value=completed(out)) as ex:
            result = guardian.scan("agy-sandbox-demo", check_git=True)
        self.assertEqual(ex.call_args[0][0], "agy-sandbox-demo")
        self.assertEqual(result.session_files, ["/home/agent/.prime"])
        self.assertEqual(result.dirty_files, [" M src/x.py"])
        self.assertEqual(result.unpushed_commits, ["+ abc fix"])
        self.assertEqual(result.local_branches, ["feature wip"])
        self.assertTrue(result.has_unsaved_work)

    def test_clean_sandbox(self):
        with patch("agy_sandbox.sandbox.exec_script", return_value=completed("")):
            self.assertFalse(guardian.scan("x", check_git=True).has_unsaved_work)

    def test_errors_are_treated_as_unsaved(self):
        with patch("agy_sandbox.sandbox.exec_script", side_effect=SandboxError("boom")):
            self.assertTrue(guardian.scan("x", check_git=False).has_unsaved_work)
        with patch("agy_sandbox.sandbox.exec_script", return_value=completed(code=1, stderr="nope")):
            self.assertEqual(guardian.scan("x", check_git=False).error, "nope")

    def test_check_for_unsaved_sandbox_work_single_exec(self):
        config = AgyConfig(project_name="demo", clone=True)
        with patch("agy_sandbox.sandbox.sandbox_exists", return_value=True), \
             patch("agy_sandbox.sandbox.exec_script", return_value=completed("SESSION /home/agent/.claude.json\n")) as ex, \
             patch("builtins.print"):
            self.assertTrue(guardian.check_for_unsaved_sandbox_work(config.sandbox_name, config))
        self.assertEqual(ex.call_count, 1)
        with patch("agy_sandbox.sandbox.sandbox_exists", return_value=False), \
             patch("agy_sandbox.sandbox.exec_script") as ex:
            self.assertFalse(guardian.check_for_unsaved_sandbox_work(config.sandbox_name, config))
        ex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
