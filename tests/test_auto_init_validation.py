import argparse
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agy_sandbox.cli import auto_init_command, main
from agy_sandbox.config import AgyConfig, load_config
from agy_sandbox.constants import AGENT_SPECS, DEFAULT_BUILD_ARGS, discover_available_agent
from agy_sandbox.prompts import build_auto_init_prompt
from agy_sandbox.spec_inspector import (
    extract_json_payload,
    generate_offline_box_spec,
    run_read_only_agent_analysis,
)


class TestAutoInitValidationSuite(unittest.TestCase):
    """
    Comprehensive validation test suite verifying auto-init codebase inspection
    across AGY, OpenCode, Oh My Pi, and local offline inspector modes.
    """

    def setUp(self):
        from agy_sandbox.constants import SUPPORTED_BUILD_ARGS
        self.supported_keys = set(SUPPORTED_BUILD_ARGS) | {"APT_PACKAGES"}

    def _verify_clean_spec_dict(self, spec_dict: dict):
        """Helper to ensure generated spec strictly adheres to supported schema."""
        self.assertIn("project_name", spec_dict)
        self.assertIn("build_args", spec_dict)
        self.assertIn("setup_scripts", spec_dict)
        self.assertIn("sbx", spec_dict)

        # Ensure NO phantom build_args exist
        build_args = spec_dict["build_args"]
        for key in build_args.keys():
            self.assertIn(
                key,
                self.supported_keys,
                f"Found unsupported/phantom build_arg key '{key}' in generated spec!",
            )

    # =========================================================================
    # Scenario 1: AGY Analyzer Validation
    # =========================================================================
    def test_agy_analyzer_flow(self):
        mock_agy_json = json.dumps({
            "project_name": "polyglot-app",
            "profile": "default",
            "build_args": {
                "PYTHON_VERSION": "3.12",
                "NODE_VERSION": "20",
                "APT_PACKAGES": "libpq-dev ffmpeg",
            },
            "setup_scripts": ["uv sync", "npm install"],
            "env": ["PORT=3000", "ENVIRONMENT=development"],
            "sbx": {
                "enabled": True,
                "agent": "agy",
                "clone": True,
                "kits": ["git+https://github.com/shelajev/agy-sbx-kit.git"],
            },
            "recommendations": {"dockerfile_needed": False, "reason": "Standard polyglot app"},
        })

        with tempfile.TemporaryDirectory() as tmp_dir:
            orig_cwd = os.getcwd()
            os.chdir(tmp_dir)
            try:
                args = argparse.Namespace(
                    analyzer="agy",
                    agent=["agy"],
                    sbx=True,
                    clone=True,
                    with_kit=[],
                    dockerfile=False,
                )

                agy_spec = AGENT_SPECS["agy"]
                with patch("agy_sandbox.cli.discover_available_agent", return_value=agy_spec), \
                     patch("agy_sandbox.cli.run_read_only_agent_analysis", return_value=extract_json_payload(mock_agy_json)):

                    auto_init_command(args)

                self.assertTrue(os.path.exists("agy.yaml"))
                cfg = load_config("agy.yaml")
                self.assertEqual(cfg.project_name, "polyglot-app")
                self.assertEqual(cfg.agent, "agy")
                self.assertEqual(cfg.build_args["PYTHON_VERSION"], "3.12")
                self.assertEqual(cfg.build_args["NODE_VERSION"], "20")
                self.assertEqual(cfg.build_args["APT_PACKAGES"], "libpq-dev ffmpeg")
                self.assertNotIn("GO_VERSION", cfg.build_args)
                self.assertNotIn("ZIG_VERSION", cfg.build_args)
            finally:
                os.chdir(orig_cwd)

    # =========================================================================
    # Scenario 2: OpenCode Analyzer Validation
    # =========================================================================
    def test_opencode_analyzer_flow(self):
        mock_opencode_json = """Here is your sandbox spec:
```json
{
  "project_name": "opencode-web-service",
  "profile": "default",
  "build_args": {
    "NODE_VERSION": "18",
    "PYTHON_VERSION": "3.11",
    "APT_PACKAGES": "cmake"
  },
  "setup_scripts": ["pnpm install", "uv sync"],
  "env": ["DATABASE_URL=postgres://localhost:5432/db"],
  "sbx": {
    "enabled": true,
    "agent": "opencode",
    "clone": true,
    "kits": ["opencode", "chrome-devtools"]
  },
  "recommendations": {"dockerfile_needed": false, "reason": "Clean spec"}
}
```
"""

        with tempfile.TemporaryDirectory() as tmp_dir:
            orig_cwd = os.getcwd()
            os.chdir(tmp_dir)
            try:
                args = argparse.Namespace(
                    analyzer="opencode",
                    agent=["opencode"],
                    sbx=True,
                    clone=True,
                    with_kit=["chrome-devtools"],
                    dockerfile=False,
                )

                opencode_spec = AGENT_SPECS["opencode"]
                with patch("agy_sandbox.cli.discover_available_agent", return_value=opencode_spec), \
                     patch("agy_sandbox.cli.run_read_only_agent_analysis", return_value=extract_json_payload(mock_opencode_json)):

                    auto_init_command(args)

                self.assertTrue(os.path.exists("agy.yaml"))
                cfg = load_config("agy.yaml")
                self.assertEqual(cfg.project_name, "opencode-web-service")
                self.assertEqual(cfg.sbx.agent, "opencode")
                self.assertEqual(cfg.build_args["NODE_VERSION"], "18")
                self.assertEqual(cfg.build_args["PYTHON_VERSION"], "3.11")
                self.assertEqual(cfg.build_args["APT_PACKAGES"], "cmake")
                self.assertIn("opencode", cfg.sbx.kits)
                self.assertIn("chrome-devtools", cfg.sbx.kits)
                self.assertNotIn(".", cfg.sbx.kits)
                self.assertNotIn("GO_VERSION", cfg.build_args)
            finally:
                os.chdir(orig_cwd)

    # =========================================================================
    # Scenario 3: Oh My Pi (OMP) Analyzer & Fallback Validation
    # =========================================================================
    def test_omp_analyzer_and_fallback_flow(self):
        omp_spec = AGENT_SPECS["omp"]

        # Test command generation for read-only OMP invocation
        cmd = omp_spec.get_read_only_cmd("Analyze code", "/tmp/project")
        self.assertEqual(cmd[0], "omp")
        self.assertIn("-p", cmd)
        self.assertIn("Analyze code", cmd)
        self.assertIn("--tools=read,grep,glob", cmd)

        # Test fallback when analyzer fails -> offline inspector
        with tempfile.TemporaryDirectory() as tmp_dir:
            orig_cwd = os.getcwd()
            os.chdir(tmp_dir)
            try:
                # Create a Rust + Go workspace
                with open(os.path.join(tmp_dir, "Cargo.toml"), "w", encoding="utf-8") as f:
                    f.write('[package]\nname = "rust-service"\nrust-version = "1.80.0"\n')
                with open(os.path.join(tmp_dir, "go.mod"), "w", encoding="utf-8") as f:
                    f.write('module go-service\n\ngo 1.22\n')

                args = argparse.Namespace(
                    analyzer="omp",
                    agent=["omp"],
                    sbx=True,
                    clone=True,
                    with_kit=[],
                    dockerfile=False,
                )

                # Mock OMP returning None -> fallback to offline scanner
                with patch("agy_sandbox.cli.discover_available_agent", return_value=omp_spec), \
                     patch("agy_sandbox.cli.run_read_only_agent_analysis", return_value=None):

                    auto_init_command(args)

                self.assertTrue(os.path.exists("agy.yaml"))
                cfg = load_config("agy.yaml")
                self.assertEqual(cfg.build_args["RUST_VERSION"], "1.80.0")
                self.assertIn("golang-go", cfg.build_args["APT_PACKAGES"])
                self.assertNotIn("GO_VERSION", cfg.build_args)
                self.assertIn("cargo fetch", cfg.setup_scripts)
                self.assertIn("go mod download", cfg.setup_scripts)
            finally:
                os.chdir(orig_cwd)

    # =========================================================================
    # Scenario 4: Smart Kit Auto-Detection in Offline Scanner
    # =========================================================================
    def test_smart_kit_autodetect(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Package with playwright dependency
            pkg_json = os.path.join(tmp_dir, "package.json")
            with open(pkg_json, "w", encoding="utf-8") as f:
                f.write('{"dependencies": {"playwright": "^1.40.0"}}\n')

            # Mojo file
            mojo_file = os.path.join(tmp_dir, "main.mojo")
            with open(mojo_file, "w", encoding="utf-8") as f:
                f.write('fn main():\n    print("Hello Mojo")\n')

            spec = generate_offline_box_spec(tmp_dir, explicit_args={"agent": "agy"})
            self._verify_clean_spec_dict(spec)

            self.assertIn("chrome-devtools", spec["sbx"]["kits"])
            self.assertIn("mojo-stdlib", spec["sbx"]["kits"])
            self.assertEqual(spec["build_args"]["MOJO_VERSION"], "latest")
            self.assertNotIn("GO_VERSION", spec["build_args"])


if __name__ == "__main__":
    unittest.main()
