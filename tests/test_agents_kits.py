import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from agy_sandbox import kits
from agy_sandbox.agents import AGENT_SPECS, discover_available_agent
from agy_sandbox.config import AgyConfig
from agy_sandbox.images import dockerfile_path
from agy_sandbox.kits import (
    KITS_DIR,
    bundled_kit_dir,
    kit_args,
    kit_volume_paths,
    list_bundled_kits,
    resolve_kit,
    run_kits,
    write_project_kit,
)

AUTH_MODE_KITS = ("agy", "claude", "codex", "opencode")


class TestAgentRegistry(unittest.TestCase):
    def test_every_agent_runs_the_same_way(self):
        """Each agent (except shell) has a bundled kit and a stacked image layer; nothing else is special."""
        for agent_id, spec in AGENT_SPECS.items():
            with self.subTest(agent=agent_id):
                if not spec.has_kit:
                    self.assertEqual(agent_id, "shell")
                    continue
                self.assertIsNotNone(bundled_kit_dir(agent_id), f"kits/{agent_id} missing")
                self.assertTrue(dockerfile_path(spec.dockerfile).exists(), f"docker/{spec.dockerfile} missing")
                self.assertTrue(spec.image_tag.endswith(":latest"))

    def test_read_only_commands(self):
        self.assertEqual(
            AGENT_SPECS["prime-agent"].read_only_cmd("Analyze", "/p"),
            ["prime-agent", "-p", "--no-session", "--no-tools", "Analyze"],
        )
        agy = AGENT_SPECS["agy"].read_only_cmd("Analyze", "/p")
        self.assertEqual(agy[:3], ["agy", "--add-dir", "/p"])
        self.assertIn("plan", agy)
        self.assertIn("--tools=read,grep,glob", AGENT_SPECS["omp"].read_only_cmd("x", "/p"))
        self.assertEqual(AGENT_SPECS["shell"].read_only_cmd("x", "/p"), [])

    def test_discover_available_agent(self):
        with patch("shutil.which", side_effect=lambda b: b in ("opencode", "claude")):
            self.assertEqual(discover_available_agent("opencode").identifier, "opencode")
            self.assertEqual(discover_available_agent().identifier, "claude")
            self.assertEqual(discover_available_agent("prime-agent").identifier, "claude")
        with patch("shutil.which", return_value=None):
            self.assertIsNone(discover_available_agent())


class TestKitResolution(unittest.TestCase):
    def test_bundled_kits_listed(self):
        names = [k["name"] for k in list_bundled_kits()]
        for expected in ("agy", "agy-proxy", "claude", "codex", "opencode", "omp", "prime-agent", "chrome-devtools", "chrome-host", "mojo-stdlib"):
            self.assertIn(expected, names)

    def test_resolve_bundled_local_and_remote(self):
        self.assertEqual(resolve_kit("omp"), str(KITS_DIR / "omp"))
        self.assertEqual(resolve_kit("git+https://example.com/kit.git"), "git+https://example.com/kit.git")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                resolve_kit(tmp)  # no spec.yaml
            Path(tmp, "spec.yaml").write_text("schemaVersion: '2'\nkind: mixin\nname: x\n")
            self.assertEqual(resolve_kit(tmp), str(Path(tmp).resolve()))
        with self.assertRaises(ValueError):
            resolve_kit("non_existent_kit")
        with self.assertRaises(ValueError):
            resolve_kit(".")

    def test_run_kits_order_and_shell(self):
        config = AgyConfig(agent="claude", kits=["chrome-devtools", "opencode"])
        self.assertEqual(
            [Path(k).name for k in run_kits(config, extra_kits=["chrome-host"])],
            ["claude", "chrome-devtools", "opencode", "chrome-host"],
        )
        self.assertEqual([Path(k).name for k in run_kits(AgyConfig(agent="shell", kits=["omp"]))], ["omp"])

    def test_proxy_overlay_only_in_proxy_managed_mode(self):
        local = [Path(k).name for k in run_kits(AgyConfig(agent="claude", kits=["agy"]))]
        self.assertEqual(local, ["claude", "agy"])
        proxied = [Path(k).name for k in run_kits(AgyConfig(agent="claude", kits=["agy"], auth_mode="proxy-managed"))]
        self.assertEqual(proxied, ["claude", "agy", "agy-proxy"])

    def test_kit_args_scoped_to_kits_that_declare_auth_mode(self):
        config = AgyConfig(agent="agy", kits=["claude", "omp", "chrome-devtools"], auth_mode="proxy-managed",
                           kit_args={"agy-proxy.auth_method": "gcp"})
        resolved = run_kits(config)
        args = kit_args(config, resolved)
        self.assertIn("agy.auth_mode=proxy-managed", args)
        self.assertIn("claude.auth_mode=proxy-managed", args)
        self.assertNotIn("omp.auth_mode=proxy-managed", args)
        self.assertNotIn("chrome-devtools.auth_mode=proxy-managed", args)
        self.assertEqual(args[-1], "agy-proxy.auth_method=gcp")

    def test_every_login_aware_kit_declares_auth_mode(self):
        for name in AUTH_MODE_KITS:
            with self.subTest(kit=name):
                self.assertTrue(kits.kit_declares_arg(KITS_DIR / name, "auth_mode"))
                text = (KITS_DIR / name / "spec.yaml").read_text()
                self.assertIn("${{ kit.args.auth_mode }}", text)

    def test_agy_proxy_kit_holds_the_credential_and_agy_kit_does_not(self):
        agy = yaml.safe_load((KITS_DIR / "agy" / "spec.yaml").read_text())
        proxy = yaml.safe_load((KITS_DIR / "agy-proxy" / "spec.yaml").read_text())
        self.assertNotIn("credentials", agy)
        self.assertEqual(proxy["credentials"][0]["service"], "antigravity")
        self.assertIn("businessaicode.googleapis.com", proxy["credentials"][0]["oauth"]["resourceHosts"])
        self.assertIn("businessaicode.googleapis.com", agy["permissions"]["network"]["allow"])
        self.assertEqual(agy["schemaVersion"], "2")
        self.assertEqual(agy["kind"], "mixin")


class TestKitVolumes(unittest.TestCase):
    def test_volume_paths_from_local_kits_only(self):
        paths = kit_volume_paths([str(KITS_DIR / "claude"), str(KITS_DIR / "omp"), "git+https://x/y.git", "/nope"])
        self.assertIn("/home/agent/.claude/projects", paths)
        self.assertIn("/home/agent/.claude/sessions", paths)
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(kit_volume_paths([str(KITS_DIR / "omp")]), [])


class TestProjectKit(unittest.TestCase):
    def test_no_setup_scripts_means_no_kit(self):
        self.assertIsNone(write_project_kit(AgyConfig(project_name="p")))

    def test_setup_scripts_become_an_install_hook(self):
        with tempfile.TemporaryDirectory() as tmp, patch("agy_sandbox.kits.STATE_DIR", tmp):
            config = AgyConfig(project_name="p", agent="claude", setup_scripts=["uv sync", "npm install"])
            path = write_project_kit(config)
            self.assertEqual(path, os.path.join(tmp, "kits", "agy-sandbox-p"))
            spec = yaml.safe_load(Path(path, "spec.yaml").read_text())
            self.assertEqual(spec["kind"], "mixin")
            command = spec["setup"]["install"][0]["command"]
            self.assertIn("uv sync\nnpm install", command)
            self.assertIn('cd "${WORKSPACE_DIR', command)
            self.assertEqual([Path(k).name for k in run_kits(config)], ["claude", "agy-sandbox-p"])


if __name__ == "__main__":
    unittest.main()
