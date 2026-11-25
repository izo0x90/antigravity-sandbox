"""
Applying agy.yaml changes to a sandbox that already exists.

  upgrade   only what sbx can do in place: add kits (`sbx kit add`), env, ports.
            Refuses anything else, naming the change and the command to use.
  recreate  snapshot the filesystem, carry kit volumes and the clone workspace
            out, destroy, create again from the snapshot, restore. For removed
            kits, changed kit arguments and a changed login mode.

Both compare the saved plan (what the sandbox was built with) against the plan
agy.yaml asks for now (compose.diff); nothing is inferred from `sbx inspect`.
"""
import shutil
import time
from dataclasses import replace
from pathlib import Path
from typing import List, Tuple

from . import compose, console, kits, paths, sandbox, workload
from .compose import SandboxPlan, kit_args_for_kit
from .config import AgyConfig
from .constants import MIN_SBX_VERSION_FOR_UPGRADE, RECREATE_TAG_PREFIX, SBX_UPGRADE_HINT
from .engine import _session, desired_plan, host_browser_for, report_missing_secrets
from .errors import AgyError, SandboxError, UpgradeRefused


def _require_exists(name: str) -> None:
    if not sandbox.exists(name):
        raise AgyError(f"Sandbox '{name}' does not exist.", hint="Create it with 'agy-sandbox up'.")


def _remedy_hint(drift) -> str:
    if drift.needs_rebuild:
        return "Run 'agy-sandbox up --rebuild'; a new image or clone mode cannot be applied to an existing sandbox."
    return "Run 'agy-sandbox recreate' to apply it while keeping the sandbox's contents."


def _finish(config: AgyConfig, plan: SandboxPlan, attach: bool) -> int:
    compose.save_plan(plan)
    report_missing_secrets(config)
    if config.host_browser.enabled:
        host_browser_for(config).start()
    return _session(plan.name, plan.env, attach)


# ---------------------------------------------------------------------------
# upgrade: in place
# ---------------------------------------------------------------------------
def upgrade(config: AgyConfig, cwd: Path, *, attach: bool = True) -> int:
    compose.validate_host_browser(config)
    version = sandbox.require()
    if version.as_tuple < MIN_SBX_VERSION_FOR_UPGRADE:
        needed = ".".join(str(n) for n in MIN_SBX_VERSION_FOR_UPGRADE)
        raise UpgradeRefused(
            [f"sbx {needed} or newer is needed for in-place upgrades (found {version})"],
            hint=SBX_UPGRADE_HINT,
        )
    name = config.sandbox_name
    _require_exists(name)
    saved = compose.load_plan(name)
    if saved is None:
        raise UpgradeRefused(
            [f"sandbox '{name}' was created by an older agy-sandbox and has no saved plan"],
            hint="Use 'agy-sandbox recreate' to rebuild it with its contents, or 'agy-sandbox up --rebuild'.",
        )
    wanted = desired_plan(config, cwd, write=False)
    drift = compose.diff(saved, wanted)
    if not drift.in_place:
        raise UpgradeRefused(drift.reasons(), hint=_remedy_hint(drift))

    console.step(f"Upgrading sandbox '{name}' in place ({config.auth_mode} logins)")
    # The saved plan advances after every successful `kit add`, so a failure half way
    # leaves a plan that matches the sandbox and a retry adds only what is left.
    workload.write(config, cwd)
    current = replace(saved, kit_args=wanted.kit_args)
    for kit in drift.added:
        console.step(f"   adding kit: {kit.name}")
        sandbox.kit_add(name, kit, sorted(kit_args_for_kit(wanted.kit_args, kit.name)))
        current = replace(current, mixins=current.mixins + (kit,))
        compose.save_plan(current)
    if not drift.added:
        console.info("   kits unchanged; applying env and ports")
    sandbox.publish_ports(name, wanted.ports)
    return _finish(config, wanted, attach)


# ---------------------------------------------------------------------------
# recreate: snapshot, carry, destroy, restore
# ---------------------------------------------------------------------------
def carry_paths(saved: SandboxPlan, wanted: SandboxPlan) -> List[str]:
    """What `sbx rm` would delete: the clone workspace and every declared kit volume."""
    carry: List[str] = [saved.workspace] if saved.clone else []
    for path in kits.volume_paths([*saved.mixins, *wanted.mixins]):
        if path not in carry:
            carry.append(path)
    return carry


def recreate(config: AgyConfig, cwd: Path, *, attach: bool = True) -> int:
    compose.validate_host_browser(config)
    sandbox.require()
    name = config.sandbox_name
    _require_exists(name)
    saved = compose.load_plan(name)
    wanted = desired_plan(config, cwd, write=False)
    if saved is None:
        info = sandbox.inspect(name)
        saved = replace(wanted, clone=info.clone, workspace=info.workspace or wanted.workspace, mixins=())
        console.warn(f"Sandbox '{name}' has no saved plan (created by an older agy-sandbox); "
                     "kit volumes are carried over only for the kits agy.yaml lists now.")
    else:
        drift = compose.diff(saved, wanted)
        if drift.needs_rebuild:
            # A snapshot carries the old image; recording a new workload as applied would hide the change forever.
            raise UpgradeRefused([r for r in drift.reasons() if "workload" in r or "clone" in r], hint=_remedy_hint(drift))
    workload.write(config, cwd)
    plan = replace(wanted, clone=saved.clone, workspace=saved.workspace)

    console.step(f"Recreating sandbox '{name}' ({config.auth_mode} logins) from a snapshot of its filesystem")
    console.info(f"   kits: {' '.join(plan.mixin_names()) or '(none)'}")

    # 1. Carry out what `sbx rm` would delete.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = paths.recreate_staging(name, stamp)
    staged: List[Tuple[str, str]] = []
    carry = carry_paths(saved, plan)
    if carry:
        console.step("Carrying out the clone workspace and kit volumes..." if plan.clone else "Carrying out kit volumes...")
        sandbox.exec_script(name, "true")  # a stopped sandbox is started so cp can read it
        for index, path in enumerate(carry):
            dest = staging / str(index)
            sandbox.copy_from(name, path, str(dest))
            staged.append((path, str(dest / Path(path).name)))

    # 2. Snapshot the whole container filesystem, then replace the sandbox.
    tag = f"{name}:{RECREATE_TAG_PREFIX}{stamp}"
    sandbox.stop(name)
    console.step(f"Snapshotting '{name}' as template '{tag}'...")
    sandbox.save_template(name, tag)
    sandbox.remove(name)
    restore_hint = [f"   Restore it with: {' '.join(sandbox.create_from_template_argv(plan, tag))}"]
    restore_hint += [f"   then: sbx cp {src} {name}:{Path(path).parent}" for path, src in staged]
    try:
        sandbox.create_from_template(plan, tag)
    except Exception:
        console.warn(f"The sandbox could not be recreated. Its snapshot is template '{tag}'"
                     + (f" and its carried-out data is under {staging}" if staged else "") + ".")
        for line in restore_hint:
            console.info(line)
        raise
    # The sandbox now has the new composition: record it before anything else can fail.
    compose.save_plan(plan)

    # 3. Put the carried-out data back and hand it to the agent user.
    try:
        for path, src in staged:
            parent = str(Path(path).parent)
            _exec_or_raise(name, f"mkdir -p '{path}' && find '{path}' -mindepth 1 -delete", user="root")
            sandbox.copy_to(src, name, parent)
            _exec_or_raise(name, f"chown -R agent:agent '{path}'", user="root")
    except Exception:
        console.warn(f"The sandbox was recreated but its carried-out data under {staging} was not fully restored.")
        for line in restore_hint[1:]:
            console.info(line)
        raise
    if staged:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            staging.parent.rmdir()
        except OSError:
            pass
        console.step("Restored the carried-out data.")

    # 4. Keep this snapshot as the rollback point; older ones for this sandbox go.
    for image in sandbox.list_templates():
        repo, old_tag = str(image.get("repository", "")), str(image.get("tag", ""))
        if (repo == name or repo.endswith("/" + name)) and old_tag.startswith(RECREATE_TAG_PREFIX) and old_tag != tag.split(":")[1]:
            sandbox.remove_template(f"{name}:{old_tag}")
    console.step(f"Recreate complete. Snapshot kept as '{tag}' for rollback.")
    return _finish(config, plan, attach)


def _exec_or_raise(name: str, script: str, user: str) -> None:
    res = sandbox.exec_script(name, script, user=user)
    if res.returncode != 0:
        raise SandboxError(f"'sbx exec {name}' failed (exit {res.returncode}) while restoring data.",
                           hint=(res.stderr or "").strip() or None, command=("sbx", "exec", name),
                           returncode=res.returncode, stderr=res.stderr or "")
