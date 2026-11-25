import unittest

from agy_sandbox import inventory


class TestInventory(unittest.TestCase):
    def test_script_prunes_runtime_dirs_and_lists_extra_paths(self):
        s = inventory.script(["/home/agent/.claude/projects"])
        self.assertIn("find / -xdev", s)
        self.assertIn("-path '/proc'", s)
        self.assertIn("'/home/agent/.claude/projects'", s)

    def test_parse_ignores_malformed_lines(self):
        entries = inventory.parse("f 10 /a\nd 4096 /b\ngarbage\nf notasize /c\n")
        self.assertEqual(set(entries), {"/a", "/b"})
        self.assertEqual(entries["/a"], inventory.Entry("f", 10))

    def test_compare_maps_the_moved_home_and_flags_only_real_losses(self):
        old = "d 4096 /root\nf 5 /root/.toolrc\nf 9 /root/.claude/session\nf 3 /root/gone\n"
        new = ("d 4096 /home/agent\nf 5 /home/agent/.toolrc\nf 9 /home/agent/.claude/session\n"
               "f 7 /home/agent/.local/bin/claude\n")   # 'gone' absent; a new file added
        diff = inventory.compare(old, new, moved_home=("/root", "/home/agent"))
        self.assertEqual(diff.missing, ["/root/gone"])
        self.assertFalse(diff.clean)
        self.assertGreaterEqual(diff.added, 1)
        self.assertEqual(diff.changed, [])

    def test_compare_reports_size_changes_and_ignores_kit_managed_paths(self):
        old = "f 10 /home/agent/.claude.json\nf 1 /usr/share/sandbox/kit/claude/kit.yaml\n"
        new = "f 20 /home/agent/.claude.json\n"
        diff = inventory.compare(old, new)
        self.assertEqual(diff.changed, ["/home/agent/.claude.json"])
        self.assertTrue(diff.clean)   # a size change is not a loss
        self.assertEqual(diff.missing, [])   # the kit-managed path is ignored, not missing

    def test_render_summarizes_and_truncates(self):
        diff = inventory.InventoryDiff(compared=100, added=2, missing=[f"/x/{i}" for i in range(50)])
        lines = inventory.render(diff)
        self.assertIn("50 missing", lines[0])
        self.assertTrue(any("more in the report file" in ln for ln in lines))
        self.assertEqual(sum(1 for ln in inventory.render(diff, full=True) if ln.startswith("   /x/")), 50)
