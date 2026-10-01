import argparse
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agy_sandbox.config import load_config
from agy_sandbox.constants import AGENT_SPECS, discover_available_agent
from agy_sandbox.prompts import build_auto_init_prompt
from agy_sandbox.spec_inspector import (
    extract_json_payload,
    generate_offline_box_spec,
    run_read_only_agent_analysis,
)


class TestAutoInitSpec(unittest.TestCase):

    def test_extract_json_payload_valid_raw(self):
        raw = '{"project_name": "test-project", "build_args": {"PYTHON_VERSION": "3.12"}}'
        result = extract_json_payload(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["project_name"], "test-project")
        self.assertEqual(result["build_args"]["PYTHON_VERSION"], "3.12")

    def test_extract_json_payload_markdown_fences(self):
        raw = """Here is the spec:
```json
{
  "project_name": "fenced-project",
  "sbx": {"enabled": true, "agent": "claude"}
}
```
Done!"""
        result = extract_json_payload(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["project_name"], "fenced-project")
        self.assertTrue(result["sbx"]["enabled"])

    def test_extract_json_payload_invalid(self):
        raw = "This is not JSON at all."
        result = extract_json_payload(raw)
        self.assertIsNone(result)

    def test_generate_offline_box_spec(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pyproject = os.path.join(tmp_dir, "pyproject.toml")
            with open(pyproject, "w", encoding="utf-8") as f:
                f.write('requires-python = ">=3.12"\n')

            pkg_json = os.path.join(tmp_dir, "package.json")
            with open(pkg_json, "w", encoding="utf-8") as f:
                f.write('{"engines": {"node": ">=18"}}\n')

            cargo_toml = os.path.join(tmp_dir, "Cargo.toml")
            with open(cargo_toml, "w", encoding="utf-8") as f:
                f.write('[package]\nname = "test"\nrust-version = "1.85.0"\n')

            go_mod = os.path.join(tmp_dir, "go.mod")
            with open(go_mod, "w", encoding="utf-8") as f:
                f.write('module test\n\ngo 1.22\n')

            makefile = os.path.join(tmp_dir, "Makefile")
            with open(makefile, "w", encoding="utf-8") as f:
                f.write('setup:\n\t@echo "setting up"\n')

            env_ex = os.path.join(tmp_dir, ".env.example")
            with open(env_ex, "w", encoding="utf-8") as f:
                f.write('PORT=8080\nENVIRONMENT=staging\nSECRET_KEY=12345\n')

            spec = generate_offline_box_spec(
                tmp_dir,
                explicit_args={
                    "agent": "opencode",
                    "sbx": True,
                    "clone": True,
                    "kits": ["opencode"],
                },
            )

            self.assertTrue(spec["sbx"]["enabled"])
            self.assertEqual(spec["sbx"]["agent"], "opencode")
            self.assertEqual(spec["build_args"]["PYTHON_VERSION"], "3.12")
            self.assertEqual(spec["build_args"]["NODE_VERSION"], "18")
            self.assertEqual(spec["build_args"]["RUST_VERSION"], "1.85.0")
            self.assertNotIn("GO_VERSION", spec["build_args"])
            self.assertIn("golang-go", spec["build_args"]["APT_PACKAGES"])
            self.assertIn("cargo fetch", spec["setup_scripts"])
            self.assertIn("go mod download", spec["setup_scripts"])
            self.assertIn("make setup", spec["setup_scripts"])
            self.assertIn("PORT=8080", spec["env"])
            self.assertIn("ENVIRONMENT=staging", spec["env"])
            # SECRET_KEY should be filtered out
            self.assertNotIn("SECRET_KEY=12345", spec["env"])
            self.assertNotIn("MOJO_VERSION", spec["build_args"])

    def test_discover_available_agent(self):
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda binary: binary in ("opencode", "claude")

            spec = discover_available_agent(preferred_agent="opencode")
            self.assertIsNotNone(spec)
            self.assertEqual(spec.identifier, "opencode")

            # Priority fallback when no preferred
            spec_fallback = discover_available_agent()
            self.assertIsNotNone(spec_fallback)
            self.assertEqual(spec_fallback.identifier, "claude")

    def test_run_read_only_agent_analysis_mock(self):
        mock_output = '{"project_name": "analyzed-app", "build_args": {"PYTHON_VERSION": "3.10"}}'
        spec = AGENT_SPECS["agy"]

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = mock_output
            mock_run.return_value = mock_proc

            result = run_read_only_agent_analysis(spec, "Analyze codebase", "/tmp")

            self.assertIsNotNone(result)
            self.assertEqual(result["project_name"], "analyzed-app")
            self.assertEqual(result["build_args"]["PYTHON_VERSION"], "3.10")

            # Verify read-only flags passed to subprocess.run
            call_args, call_kwargs = mock_run.call_args
            cmd = call_args[0]
            self.assertEqual(cmd[0], "agy")
            self.assertIn("--mode", cmd)
            self.assertIn("plan", cmd)
            self.assertIn("--print", cmd)

    def test_build_auto_init_prompt_read_only(self):
        prompt = build_auto_init_prompt(
            sbx_enabled=True,
            agent="claude",
            clone_enabled=True,
            kits=["claude", "chrome-devtools"],
        )
        self.assertIn("READ-ONLY REPOSITORY ANALYSIS", prompt)
        self.assertIn("LEVEL 1: PRE-INSTALLED BASE CONTAINER ENVIRONMENT", prompt)
        self.assertIn("ubuntu:22.04", prompt)
        self.assertIn("chrome-devtools", prompt)
        self.assertIn("opencode", prompt)
        self.assertIn("OUTPUT FORMAT", prompt)
        self.assertIn("claude", prompt)

    def test_auto_init_command_integration(self):
        from agy_sandbox.cli import auto_init_command

        with tempfile.TemporaryDirectory() as tmp_dir:
            orig_cwd = os.getcwd()
            os.chdir(tmp_dir)
            try:
                args = argparse.Namespace(
                    analyzer="claude",
                    agent=["opencode,omp,prime-agent"],
                    sbx=True,
                    clone=True,
                    with_kit=[],
                    dockerfile=False,
                )

                with patch("agy_sandbox.cli.run_read_only_agent_analysis") as mock_analysis:
                    mock_analysis.return_value = {
                        "project_name": "auto-project",
                        "build_args": {"PYTHON_VERSION": "3.11"},
                        "setup_scripts": ["uv sync"],
                        "sbx": {"enabled": True, "agent": "opencode"},
                    }
                    auto_init_command(args)

                self.assertTrue(os.path.exists("agy.yaml"))
                cfg = load_config("agy.yaml")
                self.assertEqual(cfg.project_name, "auto-project")
                self.assertEqual(cfg.sbx.agent, "opencode")
                self.assertTrue(cfg.sbx.enabled)
                self.assertNotIn("opencode", cfg.sbx.kits)
                self.assertIn("omp", cfg.sbx.kits)
                self.assertIn("prime-agent", cfg.sbx.kits)
                self.assertNotIn("claude", cfg.sbx.kits)
                self.assertNotIn(".", cfg.sbx.kits)
            finally:
                os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
