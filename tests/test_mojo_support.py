import os
import tempfile
import unittest
from pathlib import Path

from agy_sandbox.config import load_config, write_default_config
from agy_sandbox.kits import list_bundled_kits, resolve_kit
from agy_sandbox.prompts import build_auto_init_prompt


class TestMojoSupport(unittest.TestCase):
    def test_default_config_includes_mojo_version(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = os.path.join(tmp_dir, "agy.yaml")
            write_default_config(path=config_path)
            
            config = load_config(config_path)
            self.assertEqual(config.build_args, {})

    def test_bundled_kits_includes_mojo_stdlib(self):
        kits = list_bundled_kits()
        kit_names = [k["name"] for k in kits]
        self.assertIn("mojo-stdlib", kit_names)

        resolved_path = resolve_kit("mojo-stdlib")
        self.assertIsNotNone(resolved_path)
        self.assertTrue(isinstance(resolved_path, str))
        path_obj = Path(resolved_path)
        self.assertTrue(path_obj.exists())
        self.assertTrue((path_obj / "spec.yaml").exists())

    def test_auto_init_prompt_mentions_mojo(self):
        prompt = build_auto_init_prompt(sbx_enabled=True, agent="agy", clone_enabled=True)
        self.assertIn("MOJO_VERSION", prompt)
        self.assertIn("mojo-stdlib", prompt)


if __name__ == "__main__":
    unittest.main()
