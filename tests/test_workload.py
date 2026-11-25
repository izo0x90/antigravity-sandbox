import unittest
from pathlib import Path
import tempfile

import yaml

from agy_sandbox import workload
from agy_sandbox.config import AgyConfig
from agy_sandbox.constants import DEFAULT_BASE_IMAGE, KIT_DESCRIPTOR_SYNTAX

from tests.support import isolated_state


class TestRender(unittest.TestCase):
    def test_descriptor_carries_build_args_and_setup_scripts(self):
        cfg = AgyConfig(project_name="demo", build_args={"PYTHON_VERSION": "3.12"}, setup_scripts=["uv sync", "make setup"])
        desc = workload.render_descriptor(cfg)
        self.assertEqual(desc["kind"], "workload")
        self.assertEqual(desc["args"]["BASE_IMAGE"], {"default": DEFAULT_BASE_IMAGE, "buildArg": "BASE_IMAGE"})
        self.assertEqual(desc["args"]["PYTHON_VERSION"], {"default": "3.12", "buildArg": "PYTHON_VERSION"})
        types = [c["type"] for c in desc["capabilities"]]
        self.assertEqual(types, ["com.docker.sandbox/sbx@1", "com.docker.sandbox/lifecycle@1"])
        hook = desc["capabilities"][1]["config"]["install"][0]
        self.assertEqual(hook["command"][:2], ["bash", "-lc"])   # login shell: /etc/profile.d supplies the toolchain env
        self.assertIn("uv sync\nmake setup\n", hook["command"][2])
        self.assertEqual(hook["env"], ["WORKSPACE_DIR"])
        self.assertEqual(hook["user"], "agent")

    def test_descriptor_without_setup_scripts_has_no_lifecycle(self):
        desc = workload.render_descriptor(AgyConfig(project_name="demo"))
        self.assertEqual([c["type"] for c in desc["capabilities"]], ["com.docker.sandbox/sbx@1"])

    def test_include_setup_false_drops_the_install_hook(self):
        cfg = AgyConfig(project_name="demo", setup_scripts=["cargo fetch"])
        self.assertIn("lifecycle@1", [c["type"] for c in workload.render_descriptor(cfg)["capabilities"]][-1])
        no_setup = workload.render_descriptor(cfg, include_setup=False)
        self.assertEqual([c["type"] for c in no_setup["capabilities"]], ["com.docker.sandbox/sbx@1"])

    def test_write_for_snapshot_writes_a_setupless_kit_without_touching_the_normal_one(self):
        cfg = AgyConfig(project_name="demo", setup_scripts=["cargo fetch"])
        with tempfile.TemporaryDirectory() as cwd:
            normal = workload.write(cfg, Path(cwd))
            dest = Path(cwd) / "provisioned"
            ref = workload.write_for_snapshot(cfg, Path(cwd), dest)
            self.assertEqual(ref.dir, str(dest))
            self.assertNotEqual(ref.fingerprint, normal.fingerprint)   # the install hook is gone
            self.assertNotIn("cargo fetch", (dest / "workload.yaml").read_text())
            self.assertIn("cargo fetch", (Path(normal.dir) / "workload.yaml").read_text())   # normal kit untouched

    def test_descriptor_text_starts_with_the_syntax_header_and_parses(self):
        text = workload.descriptor_text(AgyConfig(project_name="demo", setup_scripts=["echo hi"]))
        self.assertTrue(text.startswith(KIT_DESCRIPTOR_SYNTAX + "\n"))
        self.assertEqual(yaml.safe_load(text)["schemaVersion"], "3")

    def test_dockerfile_base_image_is_rewritten_or_prepended(self):
        text = "ARG BASE_IMAGE=old:1\nFROM ${BASE_IMAGE}\nRUN true\n"
        self.assertEqual(workload.render_dockerfile(text, "new:2"), "ARG BASE_IMAGE=new:2\nFROM ${BASE_IMAGE}\nRUN true\n")
        self.assertEqual(workload.render_dockerfile("FROM ${BASE_IMAGE}\n", "new:2"), "ARG BASE_IMAGE=new:2\nFROM ${BASE_IMAGE}\n")
        directives = "# syntax=docker/dockerfile:1-labs\n# escape=`\nFROM ${BASE_IMAGE}\n"
        self.assertEqual(workload.render_dockerfile(directives, "new:2"),
                         "# syntax=docker/dockerfile:1-labs\n# escape=`\nARG BASE_IMAGE=new:2\nFROM ${BASE_IMAGE}\n")

    def test_render_touches_nothing_on_disk(self):
        with isolated_state() as state, tempfile.TemporaryDirectory() as cwd:
            rendered = workload.render(AgyConfig(project_name="demo"), Path(cwd))
            self.assertFalse((state / "kits").exists())
            self.assertEqual(rendered.ref, workload.write(AgyConfig(project_name="demo"), Path(cwd)))
            workload.delete("agy-sandbox-demo")
            self.assertFalse((state / "kits" / "agy-sandbox-demo").exists())

    def test_packaged_default_dockerfile_ends_as_the_agent_user(self):
        text = workload.PACKAGED_DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("ARG BASE_IMAGE=docker.io/sbx/shell-image:latest", text)
        self.assertTrue(text.rstrip().endswith("USER agent\nWORKDIR /home/agent"))


class TestWrite(unittest.TestCase):
    def test_writes_kit_dir_and_prefers_project_dockerfile(self):
        with isolated_state() as state, tempfile.TemporaryDirectory() as cwd:
            cfg = AgyConfig(project_name="demo", base_image="docker.io/me/base:1")
            ref = workload.write(cfg, Path(cwd))
            kit_dir = Path(ref.dir)
            self.assertEqual(kit_dir, state / "kits" / "agy-sandbox-demo" / "workload")
            self.assertIn("ARG BASE_IMAGE=docker.io/me/base:1", (kit_dir / "workload.dockerfile").read_text())
            self.assertTrue(ref.fingerprint.startswith("sha256:"))

            (Path(cwd) / "Dockerfile.agy").write_text("FROM ${BASE_IMAGE}\nRUN echo custom\n", encoding="utf-8")
            ref2 = workload.write(cfg, Path(cwd))
            self.assertIn("RUN echo custom", (kit_dir / "workload.dockerfile").read_text())
            self.assertNotEqual(ref.fingerprint, ref2.fingerprint)

    def test_fingerprint_is_stable_for_the_same_config(self):
        with isolated_state(), tempfile.TemporaryDirectory() as cwd:
            cfg = AgyConfig(project_name="demo", setup_scripts=["uv sync"])
            self.assertEqual(workload.write(cfg, Path(cwd)).fingerprint, workload.write(cfg, Path(cwd)).fingerprint)
            cfg.setup_scripts.append("make")
            self.assertNotEqual(workload.write(cfg, Path(cwd)).fingerprint, workload.write(AgyConfig(project_name="demo", setup_scripts=["uv sync"]), Path(cwd)).fingerprint)
