import argparse
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agy_sandbox.agents import AGENT_SPECS
from agy_sandbox.cli import auto_init_command, main
from agy_sandbox.config import load_config
from agy_sandbox.constants import SUPPORTED_BUILD_ARGS
from agy_sandbox.prompts import build_auto_init_prompt
from agy_sandbox.spec_inspector import extract_json_payload, generate_offline_box_spec, run_read_only_agent_analysis


def ns(**kw) -> argparse.Namespace:
    base = dict(analyzer=None, agent=None, clone=False, auth="sandbox-local", with_kit=[], dockerfile=False)
    base.update(kw)
    return argparse.Namespace(**base)


class InTempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def write(self, name, content):
        with open(name, "w", encoding="utf-8") as f:
            f.write(content)


class TestPayloadAndPrompt(unittest.TestCase):
    def test_extract_json_payload(self):
        self.assertEqual(extract_json_payload('{"project_name": "p"}')["project_name"], "p")
        fenced = 'Here:\n```json\n{\n  "project_name": "f",\n  "agent": "claude"\n}\n```\nDone!'
        self.assertEqual(extract_json_payload(fenced)["agent"], "claude")
        self.assertIsNone(extract_json_payload("not json"))
        self.assertIsNone(extract_json_payload(""))

    def test_prompt_describes_flat_schema(self):
        prompt = build_auto_init_prompt(agent="claude", clone_enabled=True, kits=["chrome-devtools"], auth_mode="proxy-managed")
        for fragment in ("READ-ONLY REPOSITORY ANALYSIS", "ubuntu:22.04", "MOJO_VERSION", "mojo-stdlib", "OUTPUT FORMAT",
                         '"agent": "claude"', "['claude', 'chrome-devtools']", '"auth_mode": "proxy-managed"', "opencode"):
            self.assertIn(fragment, prompt)
        self.assertNotIn('"sbx"', prompt)
        self.assertNotIn("enabled", prompt)

    def test_run_read_only_agent_analysis(self):
        proc = MagicMock(returncode=0, stdout='{"project_name": "analyzed", "build_args": {"PYTHON_VERSION": "3.10"}}')
        with patch("subprocess.run", return_value=proc) as run:
            result = run_read_only_agent_analysis(AGENT_SPECS["agy"], "Analyze", "/tmp")
        self.assertEqual(result["project_name"], "analyzed")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "agy")
        self.assertIn("plan", cmd)
        self.assertIn("--print", cmd)
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout='{"a": 1}')) as run:
            run_read_only_agent_analysis(AGENT_SPECS["prime-agent"], "Analyze", "/tmp")
        self.assertEqual(run.call_args[0][0], ["prime-agent", "-p", "--no-session", "--no-tools", "Analyze"])


class TestOfflineInspector(InTempDir):
    def test_detects_runtimes_scripts_and_env(self):
        self.write("pyproject.toml", 'requires-python = ">=3.12"\n')
        self.write("package.json", '{"engines": {"node": ">=18"}}\n')
        self.write("Cargo.toml", '[package]\nname = "t"\nrust-version = "1.85.0"\n')
        self.write("go.mod", "module t\n\ngo 1.22\n")
        self.write("Makefile", 'setup:\n\t@echo "x"\n')
        self.write(".env.example", "PORT=8080\nENVIRONMENT=staging\nSECRET_KEY=12345\n")
        spec = generate_offline_box_spec(self._tmp.name, explicit_args={"agent": "opencode", "clone": True, "kits": ["opencode"]})
        self.assertEqual(spec["agent"], "opencode")
        self.assertTrue(spec["clone"])
        self.assertEqual(spec["auth_mode"], "sandbox-local")
        self.assertEqual(spec["build_args"]["PYTHON_VERSION"], "3.12")
        self.assertEqual(spec["build_args"]["NODE_VERSION"], "18")
        self.assertEqual(spec["build_args"]["RUST_VERSION"], "1.85.0")
        self.assertIn("golang-go", spec["build_args"]["APT_PACKAGES"])
        for script in ("cargo fetch", "go mod download", "make setup"):
            self.assertIn(script, spec["setup_scripts"])
        self.assertIn("PORT=8080", spec["env"])
        self.assertNotIn("SECRET_KEY=12345", spec["env"])
        for key in spec["build_args"]:
            self.assertIn(key, set(SUPPORTED_BUILD_ARGS) | {"APT_PACKAGES"})
        self.assertNotIn("sbx", spec)

    def test_smart_kit_autodetect(self):
        self.write("package.json", '{"dependencies": {"playwright": "^1.40.0"}}\n')
        self.write("main.mojo", 'fn main():\n    print("hi")\n')
        spec = generate_offline_box_spec(self._tmp.name, explicit_args={"agent": "agy"})
        self.assertIn("chrome-devtools", spec["kits"])
        self.assertIn("mojo-stdlib", spec["kits"])
        self.assertEqual(spec["build_args"]["MOJO_VERSION"], "latest")


class TestAutoInitCommand(InTempDir):
    def test_analyzer_result_with_flag_precedence(self):
        payload = json.dumps({
            "project_name": "polyglot-app", "build_args": {"PYTHON_VERSION": "3.12", "APT_PACKAGES": "libpq-dev"},
            "setup_scripts": ["uv sync"], "env": ["PORT=3000"], "agent": "agy", "kits": ["agy"], "clone": False,
            "recommendations": {"dockerfile_needed": True, "reason": "needs cmake"},
        })
        with patch("agy_sandbox.cli.discover_available_agent", return_value=AGENT_SPECS["agy"]), \
             patch("agy_sandbox.cli.run_read_only_agent_analysis", return_value=extract_json_payload(payload)):
            auto_init_command(ns(analyzer="agy", agent=["opencode"], clone=True, with_kit=["chrome-devtools"]))
        cfg = load_config("agy.yaml")
        self.assertEqual(cfg.project_name, "polyglot-app")
        self.assertEqual(cfg.agent, "opencode")  # the flag wins over the analyzer's "agy"
        self.assertTrue(cfg.clone)
        self.assertEqual(cfg.kits, ["opencode", "agy", "chrome-devtools"])
        self.assertEqual(cfg.setup_scripts, ["uv sync"])
        self.assertEqual(cfg.build_args["APT_PACKAGES"], "libpq-dev")
        self.assertTrue(os.path.exists("Dockerfile.agy"))

    def test_legacy_shaped_analyzer_payload_still_works(self):
        payload = {"project_name": "old-shape", "sbx": {"enabled": True, "agent": "claude", "kits": ["claude", "omp"]}}
        with patch("agy_sandbox.cli.discover_available_agent", return_value=AGENT_SPECS["claude"]), \
             patch("agy_sandbox.cli.run_read_only_agent_analysis", return_value=payload):
            auto_init_command(ns(agent=["claude"]))
        cfg = load_config("agy.yaml")
        self.assertEqual(cfg.agent, "claude")
        self.assertEqual(cfg.kits, ["claude", "omp"])
        self.assertEqual(cfg.migration_notes, [])

    def test_fallback_to_offline_inspector(self):
        self.write("Cargo.toml", '[package]\nname = "r"\nrust-version = "1.80.0"\n')
        with patch("agy_sandbox.cli.discover_available_agent", return_value=AGENT_SPECS["omp"]), \
             patch("agy_sandbox.cli.run_read_only_agent_analysis", return_value=None):
            auto_init_command(ns(analyzer="omp", agent=["omp,prime-agent"], clone=True))
        cfg = load_config("agy.yaml")
        self.assertEqual(cfg.agent, "omp")
        self.assertEqual(cfg.kits, ["omp", "prime-agent"])
        self.assertEqual(cfg.build_args["RUST_VERSION"], "1.80.0")
        self.assertIn("cargo fetch", cfg.setup_scripts)

    def test_main_with_unquoted_comma_agents(self):
        with patch("agy_sandbox.cli.discover_available_agent", return_value=None), \
             patch("agy_sandbox.cli.run_read_only_agent_analysis", return_value=None):
            self.assertEqual(main(["auto-init", "--agent", "omp,", "prime-agent", "--auth", "proxy-managed"]), 0)
        cfg = load_config("agy.yaml")
        self.assertEqual(cfg.kits, ["omp", "prime-agent"])
        self.assertEqual(cfg.auth_mode, "proxy-managed")


if __name__ == "__main__":
    unittest.main()
