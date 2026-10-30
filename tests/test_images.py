import unittest
from pathlib import Path
from unittest.mock import patch

from agy_sandbox import images
from agy_sandbox.config import AgyConfig
from agy_sandbox.constants import DEFAULT_BASE_IMAGE


class TestImages(unittest.TestCase):
    def test_base_image_is_agent_free(self):
        base = images.dockerfile_path("Dockerfile.base").read_text()
        for gone in ("gnome-keyring", "dbus-x11", "antigravity.google/cli", "Antigravity.tar.gz"):
            self.assertNotIn(gone, base)
        agy = images.dockerfile_path("Dockerfile.agy").read_text()
        self.assertIn("antigravity.google/cli/install.sh", agy)
        self.assertIn("ARG BASE_IMAGE", agy)

    def test_agent_layers_are_chained(self):
        config = AgyConfig(agent="agy", kits=["omp", "prime-agent"])
        with patch("agy_sandbox.images.ensure_base_image") as ensure, patch("subprocess.run") as run:
            final = images.build_agent_layers(config)
        ensure.assert_called_once()
        self.assertEqual(final, "agy-base-prime:latest")
        cmds = [c[0][0] for c in run.call_args_list]
        self.assertEqual(len(cmds), 3)
        self.assertIn(f"BASE_IMAGE={DEFAULT_BASE_IMAGE}", cmds[0])
        self.assertIn("agy-base-agy:latest", cmds[0])
        self.assertIn("BASE_IMAGE=agy-base-agy:latest", cmds[1])
        self.assertIn("BASE_IMAGE=agy-base-omp:latest", cmds[2])

    def test_no_cache_flag(self):
        with patch("subprocess.run") as run:
            images.build_base_image(no_cache=True)
        self.assertIn("--no-cache", run.call_args[0][0])

    def test_project_image_uses_final_layer_and_build_args(self):
        config = AgyConfig(project_name="my_app", agent="claude", build_args={"PYTHON_VERSION": "3.12"})
        with patch("agy_sandbox.images.build_agent_layers", return_value="agy-base-claude:latest"), \
             patch("agy_sandbox.images.project_dockerfile", return_value=(Path("Dockerfile.default"), False)), \
             patch("subprocess.run") as run:
            self.assertEqual(images.build_project_image(config), "agy-sandbox-my-app")
        cmd = run.call_args[0][0]
        self.assertIn("BASE_IMAGE=agy-base-claude:latest", cmd)
        self.assertIn("PYTHON_VERSION=3.12", cmd)
        self.assertEqual(cmd[-1], ".")

    def test_image_id(self):
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "sha256:abc\n"
            self.assertEqual(images.image_id("x:latest"), "sha256:abc")
            run.return_value.returncode = 1
            self.assertEqual(images.image_id("x:latest"), "")


if __name__ == "__main__":
    unittest.main()
