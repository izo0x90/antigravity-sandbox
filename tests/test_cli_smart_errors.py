import io
import sys
import tempfile
import unittest
from unittest.mock import patch

from agy_sandbox.cli import main
from agy_sandbox.cli_helpers import find_best_match, preprocess_unquoted_comma_args


class TestCLISmartErrors(unittest.TestCase):

    def test_preprocess_unquoted_comma_args(self):
        raw = ["auto-init", "--analyzer", "opencode", "--agent", "omp,", "prime-agent"]
        processed = preprocess_unquoted_comma_args(raw)
        self.assertEqual(processed, ["auto-init", "--analyzer", "opencode", "--agent", "omp,prime-agent"])

    def test_preprocess_multiple_comma_args(self):
        raw = ["auto-init", "--agent", "opencode,", "omp,", "prime-agent"]
        processed = preprocess_unquoted_comma_args(raw)
        self.assertEqual(processed, ["auto-init", "--agent", "opencode,omp,prime-agent"])

    def test_find_best_match(self):
        choices = ["agy", "claude", "opencode", "codex", "omp", "prime-agent"]
        self.assertEqual(find_best_match("claud", choices), "claude")
        self.assertEqual(find_best_match("opencod", choices), "opencode")
        self.assertIsNone(find_best_match("completely_unrelated_string_12345", choices))

    def test_unquoted_comma_auto_init_execution(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            orig_cwd = sys.argv[0]
            with patch("sys.argv", ["agy-sandbox", "auto-init", "--analyzer", "opencode", "--agent", "omp,", "prime-agent"]), \
                 patch("os.getcwd", return_value=tmp_dir):
                
                with patch("agy_sandbox.cli.run_read_only_agent_analysis", return_value=None):
                    ret = main()
                    self.assertEqual(ret, 0)

    def test_smart_error_unknown_command(self):
        captured_err = io.StringIO()
        with patch("sys.stderr", captured_err), self.assertRaises(SystemExit) as cm:
            main(["autoinit"])
        self.assertEqual(cm.exception.code, 2)
        err_output = captured_err.getvalue()
        self.assertIn("Did you mean 'auto-init'?", err_output)

    def test_smart_error_unknown_agent_add(self):
        captured_err = io.StringIO()
        with patch("sys.stderr", captured_err), self.assertRaises(SystemExit) as cm:
            main(["agents", "add", "claud"])
        self.assertEqual(cm.exception.code, 1)
        err_output = captured_err.getvalue()
        self.assertIn("Did you mean 'claude'?", err_output)

    def test_smart_error_unknown_kit_add(self):
        captured_err = io.StringIO()
        with patch("sys.stderr", captured_err), self.assertRaises(SystemExit) as cm:
            main(["kits", "add", "devtools"])
        self.assertEqual(cm.exception.code, 1)
        err_output = captured_err.getvalue()
        self.assertIn("Did you mean 'chrome-devtools'?", err_output)


if __name__ == "__main__":
    unittest.main()
