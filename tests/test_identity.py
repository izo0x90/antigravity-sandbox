"""
identity: agy.yaml names the sandbox outright; the only lookup is which versions exist.
"""
import unittest
from unittest.mock import patch

from agy_sandbox import identity
from agy_sandbox.config import AgyConfig
from agy_sandbox.errors import AgyError

SBX = "agy_sandbox.sandbox."
ID = "0123456789abcdef0123456789abcdef"


def cfg(**overrides) -> AgyConfig:
    return AgyConfig(**{"project_name": "demo", "sandbox_id": ID, "sandbox_version": 2, **overrides})


def rows(*names):
    return [{"name": n, "state": "stopped"} for n in names]


class TestVersions(unittest.TestCase):
    def test_lists_only_this_identitys_versions_in_order(self):
        c = cfg()
        v = c.with_version
        listing = rows(v(3).sandbox_name, "agy-sandbox-demo", v(1).sandbox_name,
                       v(1).sandbox_name[:-1] + "2x",                                # junk after the version
                       AgyConfig(project_name="demo", sandbox_id="f" * 32).sandbox_name)  # same slug, other id
        with patch(SBX + "list_sandboxes", return_value=listing):
            self.assertEqual([n for n, _ in identity.versions(c)], [1, 3])

    def test_a_pre_identity_project_has_no_versions(self):
        with patch(SBX + "list_sandboxes", return_value=rows("agy-sandbox-demo")):
            self.assertEqual(identity.versions(AgyConfig(project_name="demo")), [])

    def test_legacy_sandbox_is_reported_only_while_it_exists(self):
        with patch(SBX + "exists", return_value=True):
            self.assertEqual(identity.legacy_sandbox(cfg()), "agy-sandbox-demo")
        with patch(SBX + "exists", return_value=False):
            self.assertIsNone(identity.legacy_sandbox(cfg()))


class TestPin(unittest.TestCase):
    def test_pins_an_existing_version(self):
        with patch(SBX + "exists", return_value=True):
            pinned = identity.pin(cfg(), 1)
        self.assertEqual(pinned.sandbox_version, 1)
        self.assertTrue(pinned.sandbox_name.endswith("-v1"))

    def test_a_missing_version_names_the_ones_that_exist_and_the_legacy_box(self):
        c = cfg()
        existing = rows(c.with_version(1).sandbox_name, c.with_version(2).sandbox_name)
        with patch(SBX + "exists", side_effect=lambda n: n == "agy-sandbox-demo"), \
             patch(SBX + "list_sandboxes", return_value=existing), self.assertRaises(AgyError) as ctx:
            identity.pin(c, 3)
        self.assertIn("Version 3 of 'demo' not found", str(ctx.exception))
        self.assertIn("Existing versions: 1, 2", ctx.exception.hint)
        self.assertIn("'agy-sandbox-demo' also still exists", ctx.exception.hint)

    def test_a_pre_identity_project_cannot_be_pinned(self):
        with self.assertRaises(AgyError) as ctx:
            identity.pin(AgyConfig(project_name="demo"), 1)
        self.assertIn("no versioned sandboxes yet", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
