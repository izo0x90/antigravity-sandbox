import os
import unittest
from unittest.mock import MagicMock, call, patch

from agy_sandbox.config import AgyConfig, SbxConfig
from agy_sandbox.constants import (
    AGENT_AGY,
    AGENT_OMP,
    AGENT_PRIME_AGENT,
    DEFAULT_BASE_IMAGE,
    OMP_BASE_IMAGE,
    PRIME_BASE_IMAGE,
)
from agy_sandbox.runners.docker import DockerRunner
from agy_sandbox.runners.sbx import SbxRunner


class TestMultiAgentStacking(unittest.TestCase):
    def test_get_requested_agent_specs_single(self):
        config = AgyConfig(agent="agy", sbx=SbxConfig(enabled=True, agent="agy", kits=[]))
        specs = config.get_requested_agent_specs()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].identifier, "agy")

    def test_get_requested_agent_specs_multi_kits(self):
        config = AgyConfig(
            agent="agy",
            sbx=SbxConfig(enabled=True, agent="agy", kits=["omp", "prime-agent"]),
        )
        specs = config.get_requested_agent_specs()
        identifiers = [s.identifier for s in specs]
        self.assertIn("agy", identifiers)
        self.assertIn("omp", identifiers)
        self.assertIn("prime-agent", identifiers)
        self.assertEqual(len(identifiers), 3)

    def test_get_requested_agent_specs_full_path_kits(self):
        config = AgyConfig(
            agent="agy",
            sbx=SbxConfig(
                enabled=True,
                agent="agy",
                kits=["/usr/local/share/agy-sandbox/kits/omp", "/opt/kits/prime-agent"],
            ),
        )
        specs = config.get_requested_agent_specs()
        identifiers = [s.identifier for s in specs]
        self.assertIn("agy", identifiers)
        self.assertIn("omp", identifiers)
        self.assertIn("prime-agent", identifiers)
        self.assertEqual(len(identifiers), 3)

    def test_build_stacked_base_image_single_omp(self):
        config = AgyConfig(
            agent="agy",
            sbx=SbxConfig(enabled=True, agent="agy", kits=["omp"]),
        )
        with patch.object(DockerRunner, "ensure_default_base_image") as mock_ensure, \
             patch("subprocess.run") as mock_run:
            final_base = DockerRunner.build_stacked_base_image(config)
            self.assertEqual(final_base, OMP_BASE_IMAGE)
            mock_ensure.assert_called_once()
            
            # Verify build command was passed --build-arg BASE_IMAGE=agy-base:latest
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertIn("--build-arg", cmd)
            self.assertIn(f"BASE_IMAGE={DEFAULT_BASE_IMAGE}", cmd)
            self.assertIn(OMP_BASE_IMAGE, cmd)

    def test_build_stacked_base_image_chained_omp_and_prime(self):
        config = AgyConfig(
            agent="agy",
            sbx=SbxConfig(enabled=True, agent="agy", kits=["omp", "prime-agent"]),
        )
        with patch.object(DockerRunner, "ensure_default_base_image") as mock_ensure, \
             patch("subprocess.run") as mock_run:
            final_base = DockerRunner.build_stacked_base_image(config)
            self.assertEqual(final_base, PRIME_BASE_IMAGE)
            mock_ensure.assert_called_once()
            self.assertEqual(mock_run.call_count, 2)

            # First build call: agy-base-omp built from agy-base:latest
            first_cmd = mock_run.call_args_list[0][0][0]
            self.assertIn(f"BASE_IMAGE={DEFAULT_BASE_IMAGE}", first_cmd)
            self.assertIn(OMP_BASE_IMAGE, first_cmd)

            # Second build call: agy-base-prime built from agy-base-omp:latest
            second_cmd = mock_run.call_args_list[1][0][0]
            self.assertIn(f"BASE_IMAGE={OMP_BASE_IMAGE}", second_cmd)
            self.assertIn(PRIME_BASE_IMAGE, second_cmd)

    def test_build_project_image_uses_final_stacked_base(self):
        config = AgyConfig(
            project_name="my_stacked_app",
            agent="agy",
            sbx=SbxConfig(enabled=True, agent="agy", kits=["omp", "prime-agent"]),
        )
        with patch.object(DockerRunner, "build_stacked_base_image", return_value=PRIME_BASE_IMAGE) as mock_stacked, \
             patch("subprocess.run") as mock_run:
            img = DockerRunner.build_project_image(config)
            self.assertEqual(img, config.image_name)
            mock_stacked.assert_called_once_with(config)

            build_cmd = mock_run.call_args[0][0]
            self.assertIn("--build-arg", build_cmd)
            self.assertIn(f"BASE_IMAGE={PRIME_BASE_IMAGE}", build_cmd)

    def test_aggregate_secret_provisioning(self):
        config = AgyConfig(
            project_name="my_secret_app",
            auth_mode="sbx_proxy",
            agent="agy",
            sbx=SbxConfig(enabled=True, agent="agy", kits=["prime-agent"]),
        )
        with patch("subprocess.run") as mock_run:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_run.return_value = mock_res

            SbxRunner.ensure_secret_configured(config)

            # Aggregated services for agy ("google") + prime-agent ("prime", "anthropic", "openai", "google")
            expected_services = {"google", "prime", "anthropic", "openai"}
            called_services = set()
            for call_item in mock_run.call_args_list:
                cmd = call_item[0][0]
                if len(cmd) >= 5 and cmd[0] == "sbx" and cmd[1] == "secret" and cmd[2] == "set":
                    called_services.add(cmd[4])

            self.assertEqual(called_services, expected_services)

    def test_run_up_bypasses_build_when_sandbox_exists(self):
        from agy_sandbox.engine import run_up
        config = AgyConfig(
            project_name="my_existing_app",
            agent="agy",
            sbx=SbxConfig(enabled=True, agent="agy", kits=[]),
        )
        with patch("agy_sandbox.engine.sandbox_exists", return_value=True) as mock_exists, \
             patch("agy_sandbox.engine.build_project_image") as mock_build, \
             patch("agy_sandbox.engine.run_up_sbx") as mock_run_sbx:
            run_up(config, rebuild=False)
            mock_exists.assert_called_once_with(config.sandbox_name)
            mock_build.assert_not_called()
            mock_run_sbx.assert_called_once_with(config, image_name="", rebuild=False)


if __name__ == "__main__":
    unittest.main()
