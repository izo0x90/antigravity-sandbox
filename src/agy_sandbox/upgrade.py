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
from typing import List, Sequence, Tuple

from . import compose, console, inventory, kits, paths, sandbox, snapshot, workload
from .compose import SandboxPlan, kit_args_for_kit
from .config import AgyConfig
from .constants import MIN_SBX_VERSION_FOR_UPGRADE, RECREATE_TAG_PREFIX, SBX_UPGRADE_HINT
from .engine import _session, desired_plan, host_browser_for, report_missing_secrets
from .errors import AgyError, SandboxError, UpgradeRefused

MIGRATE_HOME_SCRIPT = Path(__file__).resolve().parent / "migrate_home.sh"


def _snapshot_workload(config: AgyConfig, cwd: Path, staging: Path):
    """
    The workload kit with its setup_scripts install hook removed, for a `create --template`
    that seeds an already-provisioned snapshot. Re-running setup on a snapshot is redundant,
    and when the session user was converted root->agent it fails (the hook runs as agent
    before the old root home's per-user toolchain state has been migrated).
    """
    # Named "workload" (not e.g. "workload-provisioned") so `sbx ls` shows the new box's agent
    # exactly as a freshly-built 0.6 box; the kit dir is temporary and removed after create.
    return workload.write_for_snapshot(config, cwd, staging / "workload")


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
    # The snapshot already carries everything setup_scripts produced, so create from it with a
    # workload that has no install hook; the saved plan keeps the real workload for upgrades.
    staging.mkdir(parents=True, exist_ok=True)
    create_plan = replace(plan, workload=_snapshot_workload(config, cwd, staging))
    restore_hint = [f"   Restore it with: {' '.join(sandbox.create_from_template_argv(create_plan, tag))}"]
    restore_hint += [f"   then: sbx cp {src} {name}:{Path(path).parent}" for path, src in staged]
    try:
        sandbox.create_from_template(create_plan, tag)
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


# ---------------------------------------------------------------------------
# recreate --as: build a new sandbox beside the old one, keeping the old untouched
# ---------------------------------------------------------------------------
def _migrate_root_home(name: str) -> List[str]:
    """
    Relocates a legacy root session's home into /home/agent, inside the running
    sandbox, and returns the report lines (MOVED/KEPT/REF). Raises on failure.
    """
    script = MIGRATE_HOME_SCRIPT.read_text(encoding="utf-8")
    res = sandbox.exec_script(name, script, user="root")
    lines = [ln for ln in (res.stdout or "").splitlines()
             if ln.split(" ", 1)[0] in ("MOVED", "KEPT", "RELINK", "FIXVENV", "REF")]
    if res.returncode != 0:
        raise SandboxError(f"Migrating /root into /home/agent failed (exit {res.returncode}).",
                           hint=(res.stderr or "").strip() or None, command=("sbx", "exec", name),
                           returncode=res.returncode, stderr=res.stderr or "")
    return lines


def _inventory(name: str, extra_paths: Sequence[str] = ()) -> str:
    """A full file listing of a sandbox's root filesystem plus any separately-mounted paths."""
    return sandbox.exec_script(name, inventory.script(extra_paths)).stdout or ""


def recreate_as(config: AgyConfig, cwd: Path, *, new_name: str, attach: bool = True) -> int:
    """
    Snapshots the configured sandbox and builds a second sandbox named `new_name` from
    that snapshot, with the 0.6 kits. The original sandbox is never modified or removed.

    When the original starts its sessions as root (built by agy-sandbox 0.5), the
    snapshot's start-user is flipped to agent and the old root home is migrated into
    /home/agent, so the copy comes back on the agent user with its filesystem intact.
    """
    compose.validate_host_browser(config)
    sandbox.require()
    old_name = config.sandbox_name
    _require_exists(old_name)
    new_sandbox = config.sandbox_name_for(new_name)
    if sandbox.exists(new_sandbox):
        raise AgyError(f"Sandbox '{new_sandbox}' already exists.",
                       hint=f"Choose another name, or remove it with 'sbx rm -f {new_sandbox}'.")

    saved = compose.load_plan(old_name)
    wanted = desired_plan(config, cwd, write=False)
    if saved is None:
        info = sandbox.inspect(old_name)
        saved = replace(wanted, clone=info.clone, workspace=info.workspace or wanted.workspace, mixins=())
    workload.write(config, cwd)
    plan = replace(wanted, name=new_sandbox, clone=saved.clone, workspace=saved.workspace)

    console.step(f"Building '{new_sandbox}' beside '{old_name}' ({config.auth_mode} logins); '{old_name}' is left untouched")
    console.info(f"   kits: {' '.join(plan.mixin_names()) or '(none)'}")

    # 1. Carry the clone workspace and kit volumes out of the OLD sandbox (read only to it).
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = paths.recreate_staging(new_sandbox, stamp)
    staging.mkdir(parents=True, exist_ok=True)   # holds the snapshot tar and any carried-out data
    staged: List[Tuple[str, str]] = []
    carry = carry_paths(saved, plan)
    if carry:
        console.step("Copying the clone workspace and kit volumes out..." if plan.clone else "Copying kit volumes out...")
        sandbox.exec_script(old_name, "true")
        for index, path in enumerate(carry):
            dest = staging / str(index)
            sandbox.copy_from(old_name, path, str(dest))
            staged.append((path, str(dest / Path(path).name)))

    # 2. Snapshot the OLD filesystem to a file, flip its start-user to agent, load it as the new tag.
    old_text = _inventory(old_name)   # captured while the old box is up, before we stop it
    sandbox.stop(old_name)
    tar = staging / "snapshot.tar"
    rolled = staging / "snapshot-agent.tar"
    tag = f"{new_name}:{RECREATE_TAG_PREFIX}{stamp}"
    export_tag = f"{old_name}:{RECREATE_TAG_PREFIX}{stamp}-export"
    console.step(f"Snapshotting '{old_name}' and retagging it as '{tag}'...")
    # save_template needs a tag of its own to also write the tar; the old box is kept, so that
    # intermediate snapshot is redundant and removed once the tar is in hand.
    sandbox.save_template(old_name, export_tag, output=str(tar))
    sandbox.remove_template(export_tag)
    info = snapshot.inspect(str(tar))
    convert = not info.starts_as_agent
    snapshot.rewrite(str(tar), str(rolled), user="agent" if convert else None, ref=tag)
    sandbox.load_template(str(rolled))
    tar.unlink(missing_ok=True)
    rolled.unlink(missing_ok=True)
    if convert:
        console.info(f"   the old sandbox started sessions as '{info.user}'; the copy will start as 'agent'")

    # 3. Create the NEW sandbox from the (converted) snapshot. Once it exists it owns its own
    # copy of the layers, and the untouched old sandbox is the rollback, so the snapshot template
    # is redundant and removed. On failure it is kept so the create can be retried from it.
    # The snapshot already carries everything setup_scripts produced; create from it with a
    # workload that has no install hook (re-running it as the converted agent user would fail).
    create_plan = replace(plan, workload=_snapshot_workload(config, cwd, staging))
    try:
        sandbox.create_from_template(create_plan, tag)
    except Exception:
        console.warn(f"'{new_sandbox}' could not be created. Its snapshot is template '{tag}'"
                     + (f" and the carried-out data is under {staging}" if staged else "")
                     + f". '{old_name}' is unchanged.")
        raise
    compose.save_plan(plan)
    sandbox.remove_template(tag)

    # 4. Restore the carried-out volumes and clone, THEN migrate the old root home on top.
    # Order matters: for a legacy box the kit volumes under /home/agent were empty (the root
    # session never used them) and the real session state is in /root, so the home move must
    # land last or the (empty) volume restore would overwrite it.
    report: List[str] = []
    try:
        for path, src in staged:
            parent = str(Path(path).parent)
            _exec_or_raise(new_sandbox, f"mkdir -p '{path}' && find '{path}' -mindepth 1 -delete", user="root")
            sandbox.copy_to(src, new_sandbox, parent)
            _exec_or_raise(new_sandbox, f"chown -R agent:agent '{path}'", user="root")
        if convert:
            console.step("Moving the old root home into /home/agent...")
            report = _migrate_root_home(new_sandbox)
    except Exception:
        console.warn(f"'{new_sandbox}' was created but its data was not fully migrated. '{old_name}' is unchanged; "
                     f"inspect '{new_sandbox}' or remove it with 'sbx rm -f {new_sandbox}' and retry.")
        raise
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.parent.rmdir()
    except OSError:
        pass

    # 5. Verify nothing was lost: compare a full file listing of the old box against the new one.
    new_text = _inventory(new_sandbox, extra_paths=kits.volume_paths(plan.mixins))
    moved_home = ("/root", "/home/agent") if convert else None
    diff = inventory.compare(old_text, new_text, moved_home=moved_home)
    report_path = paths.state_dir() / f"recreate-{new_name}-{stamp}.txt"
    _write_report(report_path, old_name, new_sandbox, diff, report, convert)
    for line in inventory.render(diff):
        (console.warn if not diff.clean else console.info)("   " + line)
    refs = sorted({ln[4:] for ln in report if ln.startswith("REF ")})
    if refs:
        console.warn(f"   {len(refs)} moved file(s) still mention '/root/' and may need editing (see the report).")
    console.info(f"   full comparison written to {report_path}")
    console.step(f"Done. '{new_sandbox}' is ready and '{old_name}' is untouched. "
                 f"When you are satisfied, remove the old one with 'sbx rm {old_name}'.")
    return _finish(config, plan, attach)


def _write_report(path: Path, old_name: str, new_name: str, diff, move_report: List[str], converted: bool) -> None:
    """Writes the complete before/after comparison and move log for the person to review."""
    kept = [ln[5:] for ln in move_report if ln.startswith("KEPT ")]
    relinked = sorted({ln[7:] for ln in move_report if ln.startswith("RELINK ")})
    fixvenv = sorted({ln[8:] for ln in move_report if ln.startswith("FIXVENV ")})
    refs = sorted({ln[4:] for ln in move_report if ln.startswith("REF ")})
    lines = [f"recreate --as report: {old_name} -> {new_name}", ""]
    lines.append("Session user converted from root to agent." if converted else "Old sandbox already ran as agent.")
    if converted:
        lines.append("The old root home was moved into /home/agent.")
        if kept:
            lines += ["", f"Existing agent files kept beside the moved ones ({len(kept)}):"] + [f"   {k}" for k in kept]
        if relinked:
            lines += ["", f"Symlinks repointed from /root into /home/agent ({len(relinked)}):"] + [f"   {r}" for r in relinked]
        if fixvenv:
            lines += ["", f"Virtualenv files whose /root paths were rewritten to /home/agent ({len(fixvenv)}):"] + [f"   {r}" for r in fixvenv]
        if refs:
            lines += ["", f"Moved files that still mention /root/ ({len(refs)}):"] + [f"   {r}" for r in refs]
    lines += ["", "File comparison (old sandbox vs new sandbox):"] + inventory.render(diff, full=True)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
