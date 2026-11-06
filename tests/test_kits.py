"""
Invariants over the bundled v3 kits plus the reference resolver. The descriptor
checks are what keeps a kit edit from silently breaking the login model.
"""
import os
import re
import tempfile
import unittest
from pathlib import Path

from agy_sandbox import kits
from agy_sandbox.agents import AGENT_SPECS
from agy_sandbox.constants import KIT_DESCRIPTOR_SYNTAX
from agy_sandbox.errors import KitError
from agy_sandbox.kits import (
    CREDENTIAL_CAPABILITY,
    LIFECYCLE_CAPABILITY,
    NETWORK_CAPABILITY,
    KitRef,
    bundled_kit,
    bundled_kits,
    capabilities,
    credentials_kit,
    resolve,
    volume_paths,
)

# Variables every hook may read without declaring them (lifecycle@1 platform baseline)
BASELINE = {"PATH", "HOME", "HOSTNAME", "TERM", "PWD", "OLDPWD", "SHLVL", "_"}
VAR_REF = re.compile(r"(?<!\\)\$\{?([A-Za-z_][A-Za-z0-9_]*)")
ASSIGNMENT = re.compile(r"(?:^|\s|;)(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)
LOOP_VAR = re.compile(r"\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\b")
JQ_ARG = re.compile(r"--arg\s+([A-Za-z_][A-Za-z0-9_]*)")   # `$name` inside a jq program
KIT_ARG = re.compile(r"\$\{\{\s*kit\.args\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def hook_commands(descriptor: dict):
    for entry in capabilities(descriptor, LIFECYCLE_CAPABILITY):
        cfg = entry.get("config") or {}
        for phase in ("install", "startup"):
            for hook in cfg.get(phase) or []:
                yield phase, hook


def undeclared_variables(hook: dict) -> set:
    command = hook["command"]
    script = command if isinstance(command, str) else " ".join(command)
    local = set(ASSIGNMENT.findall(script)) | set(LOOP_VAR.findall(script)) | set(JQ_ARG.findall(script))
    declared = set(hook.get("env") or []) | BASELINE | local
    return {v for v in VAR_REF.findall(script) if v not in declared and not v.isdigit()}


class TestBundledDescriptors(unittest.TestCase):
    def setUp(self):
        self.kits = bundled_kits(include_internal=True)

    def test_every_kit_is_v3_with_the_syntax_header(self):
        self.assertGreater(len(self.kits), 10)
        for kit in self.kits.values():
            self.assertEqual(kit.descriptor["schemaVersion"], "3", kit.name)
            self.assertEqual(kit.descriptor["kind"], "mixin", kit.name)
            first = kits.descriptor_path(kit.path).read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first, KIT_DESCRIPTOR_SYNTAX, kit.name)

    def test_every_agent_has_a_kit_and_agent_kits_declare_no_credentials(self):
        for identifier, spec in AGENT_SPECS.items():
            if not spec.has_kit:
                continue
            self.assertIn(identifier, self.kits, identifier)
            self.assertEqual(capabilities(self.kits[identifier].descriptor, CREDENTIAL_CAPABILITY), [], identifier)

    def test_credential_kits_declare_credentials_and_their_inject_domains(self):
        found = [k for k in self.kits.values() if k.internal]
        self.assertEqual(sorted(k.name for k in found),
                         ["agy-credentials", "claude-credentials", "codex-credentials", "opencode-credentials"])
        for kit in found:
            creds = capabilities(kit.descriptor, CREDENTIAL_CAPABILITY)
            self.assertTrue(creds, kit.name)
            allowed = set()
            for net in capabilities(kit.descriptor, NETWORK_CAPABILITY):
                allowed |= set((net["config"].get("runtime") or {}).get("allow") or [])
            for cred in creds:
                self.assertTrue(cred.get("optional"), f"{kit.name}: credentials must be optional")
                cfg = cred["config"]
                self.assertEqual(cfg["phase"], "runtime", kit.name)
                for rule in (cfg.get("apiKey") or {}).get("inject") or []:
                    self.assertIn(rule["domain"], allowed, f"{kit.name}: inject domain not allowed")
                for host in (cfg.get("oauth") or {}).get("resourceHosts") or []:
                    self.assertIn(host, allowed, f"{kit.name}: resource host not allowed")
            # The kit it belongs to must exist and the pairing must resolve
            base = bundled_kit(kit.name[: -len("-credentials")])
            self.assertIsNotNone(base, kit.name)
            self.assertEqual(credentials_kit(base.ref()).name, kit.name)

    def test_hooks_declare_every_variable_they_read(self):
        for kit in self.kits.values():
            for phase, hook in hook_commands(kit.descriptor):
                self.assertEqual(undeclared_variables(hook), set(), f"{kit.name} {phase}: {hook.get('description')}")
                self.assertIn("description", hook, f"{kit.name} {phase} hook without description")

    def test_kit_args_referenced_in_descriptors_are_declared(self):
        for kit in self.kits.values():
            text = kits.descriptor_path(kit.path).read_text(encoding="utf-8")
            declared = set(kit.descriptor.get("args") or {})
            for arg in KIT_ARG.findall(text):
                self.assertIn(arg, declared, f"{kit.name} references undeclared arg {arg}")

    def test_no_kit_declares_a_login_mode_argument(self):
        for kit in self.kits.values():
            self.assertNotIn("auth_mode", kit.descriptor.get("args") or {}, kit.name)

    def test_dockerfiles_pair_with_descriptors_and_keep_home_root_owned(self):
        for kit in self.kits.values():
            dockerfile = kit.path / f"{kit.name}.dockerfile"
            if not dockerfile.is_file():
                continue
            text = dockerfile.read_text(encoding="utf-8")
            self.assertNotIn("COPY --chown", text, f"{kit.name}: stage under /out and chown there instead")
            self.assertNotRegex(text, r"chown -R 1000:1000 /out/home(\s|$)", f"{kit.name}: /home must stay root-owned")
            self.assertTrue(text.startswith("# syntax=docker/dockerfile:1"), kit.name)

    def test_mixins_never_declare_workload_only_things(self):
        for kit in self.kits.values():
            self.assertEqual(capabilities(kit.descriptor, "com.docker.sandbox/sbx@1"), [], kit.name)
            for ctx in capabilities(kit.descriptor, "com.docker.sandbox/agent-context@1"):
                self.assertNotIn("filename", ctx.get("config") or {}, kit.name)
            for entry in capabilities(kit.descriptor, LIFECYCLE_CAPABILITY):
                cfg = entry.get("config") or {}
                self.assertTrue(cfg.get("install") or cfg.get("startup") or cfg.get("files"), f"{kit.name}: empty lifecycle")
            for name, arg in (kit.descriptor.get("args") or {}).items():
                self.assertFalse("default" in arg and arg.get("required"), f"{kit.name}.{name}: default and required")

    def test_credential_kits_are_declaration_only_and_require_their_agent(self):
        for kit in self.kits.values():
            if not kit.internal:
                continue
            self.assertEqual(capabilities(kit.descriptor, LIFECYCLE_CAPABILITY), [], kit.name)
            self.assertFalse((kit.path / f"{kit.name}.dockerfile").exists(), kit.name)
            base = self.kits[kit.name[: -len("-credentials")]]
            self.assertEqual(kit.descriptor.get("requires"), base.descriptor.get("provides"), kit.name)
            services = [s for s, _ in kits.credential_services(kit.descriptor)]
            self.assertEqual(len(services), len(set(services)), f"{kit.name}: duplicate service")

    def test_public_listing_hides_credential_kits(self):
        public = bundled_kits()
        self.assertFalse(any(k.internal for k in public.values()))
        self.assertIn("claude", public)
        self.assertIn("chrome-devtools", public)


class TestResolve(unittest.TestCase):
    def test_bundled_names_resolve_to_their_directory(self):
        ref = resolve("claude")
        self.assertTrue(ref.bundled)
        self.assertEqual(ref.name, "claude")
        self.assertTrue(ref.source.endswith(os.path.join("kits", "claude")))

    def test_remote_references_pass_through_with_a_derived_name(self):
        self.assertEqual(resolve("git+https://github.com/org/kits.git#dir=mcp-postgres").name, "mcp-postgres")
        self.assertEqual(resolve("docker.io/sbx/playwright:1.61.1").name, "playwright")
        self.assertEqual(resolve("oci://ghcr.io/org/kit@sha256:abc").name, "kit")
        self.assertFalse(resolve("docker.io/sbx/playwright:1.61.1").bundled)

    def test_local_directory_needs_a_v3_descriptor_named_after_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit_dir = Path(tmp) / "mykit"
            kit_dir.mkdir()
            with self.assertRaises(KitError):
                resolve(str(kit_dir))
            (kit_dir / "mykit.yaml").write_text('schemaVersion: "2"\nkind: mixin\n', encoding="utf-8")
            with self.assertRaises(KitError):
                resolve(str(kit_dir))
            (kit_dir / "mykit.yaml").write_text(
                'schemaVersion: "3"\nkind: mixin\ncapabilities:\n  - type: com.docker.sandbox/volume@1\n    config: {path: /home/agent/.x, size: 1g}\n',
                encoding="utf-8",
            )
            ref = resolve(str(kit_dir))
            self.assertEqual((ref.name, ref.bundled), ("mykit", False))
            self.assertEqual(volume_paths([ref]), ["/home/agent/.x"])

    def test_unknown_names_and_shell_raise_with_hints(self):
        with self.assertRaises(KitError) as ctx:
            resolve("nope")
        self.assertIn("claude", ctx.exception.hint)
        with self.assertRaises(KitError):
            resolve("shell")
        with self.assertRaises(KitError):
            resolve(".")

    def test_absolute_paths_are_never_bundled_names(self):
        self.assertIsNone(bundled_kit("/tmp"))
        self.assertIsNone(credentials_kit(KitRef(name="claude", source="/elsewhere/claude", bundled=False)))

    def test_volume_paths_from_bundled_claude(self):
        paths = volume_paths([resolve("claude"), resolve("chrome-devtools")])
        self.assertIn("/home/agent/.claude/projects", paths)
        self.assertEqual(len(paths), 5)
