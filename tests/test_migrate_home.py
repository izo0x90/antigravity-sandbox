"""
migrate_home.sh moves a legacy root session's home into /home/agent. It recurses to
merge directories, so it is exercised here as a real shell run against a temp tree with
MIGRATE_SRC/MIGRATE_DST/MIGRATE_OWNER overridden (it normally runs as root in a sandbox).
These guard the two bugs found migrating a real box: a recursion that clobbered its own
loop variable, and absolute symlinks into /root that dangled once their target moved.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "src" / "agy_sandbox" / "migrate_home.sh"


def run_migrate(src: Path, dst: Path):
    env = dict(os.environ, MIGRATE_SRC=str(src), MIGRATE_DST=str(dst),
               MIGRATE_OWNER=f"{os.getuid()}:{os.getgid()}")
    res = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True)
    report = {"MOVED": [], "KEPT": [], "RELINK": [], "FIXVENV": [], "REF": []}
    for line in res.stdout.splitlines():
        head, _, rest = line.partition(" ")
        if head in report:
            report[head].append(rest)
    return res, report


class TestMigrateHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / "root"
        self.dst = Path(self.tmp.name) / "agent"

    def tearDown(self):
        self.tmp.cleanup()

    def test_merges_dirs_without_clobbering_the_walk_and_keeps_every_file(self):
        # A directory (.local/bin) that exists on both sides must merge: the agent's own file
        # stays, root's sibling moves in, and the walk then still reaches the next sibling
        # (.local/share) — the recursion bug relocated .local/share *inside* the moved symlink.
        (self.src / ".local/bin").mkdir(parents=True)
        (self.src / ".local/share/uv/tools/reg/bin").mkdir(parents=True)
        real = self.src / ".local/share/uv/tools/reg/bin/registry"
        real.write_text("#!/bin/sh\necho hi\n")
        (self.src / ".local/bin/registry").symlink_to(real)   # absolute symlink into $SRC
        (self.dst / ".local/bin").mkdir(parents=True)
        (self.dst / ".local/bin/agy").write_text("#agy")

        res, report = run_migrate(self.src, self.dst)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual((self.dst / ".local/bin/agy").read_text(), "#agy")          # agent file kept
        self.assertTrue((self.dst / ".local/share/uv/tools/reg/bin/registry").is_file())  # share merged, not nested
        self.assertFalse((self.dst / ".local/bin/registry/share").exists())          # the old clobber bug

    def test_repoints_symlinks_that_pointed_into_the_old_home(self):
        (self.src / ".local/bin").mkdir(parents=True)
        (self.src / ".local/share/tool").mkdir(parents=True)
        target = self.src / ".local/share/tool/exe"
        target.write_text("binary")
        (self.src / ".local/bin/exe").symlink_to(target)                 # absolute -> into $SRC
        (self.src / ".local/share/rel").symlink_to("tool")               # relative, moves with its target

        res, report = run_migrate(self.src, self.dst)
        self.assertEqual(res.returncode, 0, res.stderr)
        link = self.dst / ".local/bin/exe"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(self.dst / ".local/share/tool/exe"))  # repointed into $DST
        self.assertEqual(link.read_text(), "binary")                      # resolves, does not dangle
        self.assertIn(".local/bin/exe", report["RELINK"])
        self.assertEqual(os.readlink(self.dst / ".local/share/rel"), "tool")  # relative link untouched

    def test_relocates_a_virtualenv_by_rewriting_its_baked_in_paths(self):
        # A uv tool / venv bakes $SRC into its shebang, pyvenv.cfg and activate script; once
        # moved, those must point at $DST or the interpreter dangles. Only venv files change —
        # a log that merely mentions the old path is left for the REF scan, not rewritten.
        tool = self.src / ".local/share/uv/tools/reg"
        (tool / "bin").mkdir(parents=True)
        exe = tool / "bin" / "reg"
        exe.write_text(f"#!{tool}/bin/python\nprint('hi')\n")
        exe.chmod(0o755)
        (tool / "pyvenv.cfg").write_text(f"home = {self.src}/.local/share/uv/python/c/bin\n")
        (tool / "uv-receipt.toml").write_text(f'path = "{self.src}/.local/share/uv/tools/reg"\n')  # venv-root metadata
        (tool / "bin" / "activate").write_text(f"VIRTUAL_ENV='{tool}'\n")
        (tool / "lib").mkdir()
        (tool / "lib" / "pkgdata.txt").write_text(f"docstring example: {self.src}/x\n")  # lib data: must NOT change
        logs = self.src / ".omp/logs"
        logs.mkdir(parents=True)
        (logs / "run.log").write_text(f"started in {self.src}/work\n")   # a log, not a venv

        res, report = run_migrate(self.src, self.dst)
        self.assertEqual(res.returncode, 0, res.stderr)
        new_tool = self.dst / ".local/share/uv/tools/reg"
        self.assertEqual((new_tool / "bin/reg").read_text().splitlines()[0], f"#!{new_tool}/bin/python")
        self.assertIn(f"home = {self.dst}/.local/share/uv/python/c/bin", (new_tool / "pyvenv.cfg").read_text())
        self.assertIn(f"VIRTUAL_ENV='{new_tool}'", (new_tool / "bin/activate").read_text())
        self.assertTrue(os.access(new_tool / "bin/reg", os.X_OK))          # stays executable
        self.assertIn(".local/share/uv/tools/reg/pyvenv.cfg", report["FIXVENV"])
        self.assertIn(f'path = "{new_tool}"', (new_tool / "uv-receipt.toml").read_text())   # venv-root metadata fixed
        self.assertEqual((new_tool / "lib/pkgdata.txt").read_text(), f"docstring example: {self.src}/x\n")  # lib untouched
        self.assertEqual((self.dst / ".omp/logs/run.log").read_text(), f"started in {self.src}/work\n")  # log untouched
        self.assertTrue(any(p.endswith(".omp/logs/run.log") for p in report["REF"]))  # flagged, not rewritten

    def test_venv_fix_never_writes_through_a_symlink(self):
        # A bin/ entry that is a symlink to a text file elsewhere must not get that file rewritten.
        tool = self.src / ".local/share/uv/tools/reg"
        (tool / "bin").mkdir(parents=True)
        (tool / "lib").mkdir()
        (tool / "pyvenv.cfg").write_text("home = /usr/bin\n")
        (tool / "lib" / "notes.txt").write_text(f"see {self.src}/x\n")
        (tool / "bin" / "notes").symlink_to("../lib/notes.txt")           # relative, stays valid after the move

        res, report = run_migrate(self.src, self.dst)
        self.assertEqual(res.returncode, 0, res.stderr)
        new_tool = self.dst / ".local/share/uv/tools/reg"
        self.assertEqual((new_tool / "lib/notes.txt").read_text(), f"see {self.src}/x\n")   # untouched
        self.assertEqual(report["FIXVENV"], [])

    def test_name_clash_preserves_the_agent_file_as_pre_migration(self):
        self.src.mkdir(parents=True)
        (self.src / ".bashrc").write_text("root")
        self.dst.mkdir(parents=True)
        (self.dst / ".bashrc").write_text("agent")

        res, report = run_migrate(self.src, self.dst)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual((self.dst / ".bashrc").read_text(), "root")                 # root's moved in
        self.assertEqual((self.dst / ".bashrc.pre-migration").read_text(), "agent")  # agent's preserved
        self.assertIn(".bashrc.pre-migration", report["KEPT"])


if __name__ == "__main__":
    unittest.main()
