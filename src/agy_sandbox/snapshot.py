"""
Snapshot images as files.

`sbx template save --output` writes an OCI image layout as a tar: `index.json` -> a
manifest blob -> a config blob + layer blobs, plus a docker-archive `manifest.json`
for compatibility. Two facts about a sandbox live in the config blob, not on its
disk: the user a session starts as and the image's name. This module reads and
rewrites those two fields and nothing else; every layer is carried byte for byte.

Used by `recreate` to move a sandbox made by an older agy-sandbox, whose sessions
started as root, onto the agent user without touching its filesystem.
"""
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from typing import Dict, Optional

from .errors import SandboxError

AGENT_USER = "agent"
AGENT_UID = "1000"
ROOT_USER = "root"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
LIBRARY_PREFIX = "docker.io/library/"


@dataclass(frozen=True)
class SnapshotInfo:
    user: str          # the config's User, normalised: "" (unset) reads as root
    ref: str           # repository:tag the archive is tagged with
    layers: int

    @property
    def starts_as_agent(self) -> bool:
        return self.user.split(":")[0] in (AGENT_USER, AGENT_UID)


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _blob_path(digest: str) -> str:
    return "blobs/" + digest.replace(":", "/")


class _Archive:
    def __init__(self, path: str) -> None:
        self.path = path
        self.tar = tarfile.open(path)
        self.members = {m.name: m for m in self.tar.getmembers()}

    def close(self) -> None:
        self.tar.close()

    def read(self, name: str) -> bytes:
        member = self.members.get(name)
        if member is None:
            raise SandboxError(f"Snapshot '{self.path}' has no '{name}'; not an OCI image archive.")
        return self.tar.extractfile(member).read()

    def json(self, name: str) -> dict:
        return json.loads(self.read(name))

    def manifest_descriptor(self) -> dict:
        index = self.json("index.json")
        manifests = [m for m in index.get("manifests", []) if m.get("mediaType", OCI_MANIFEST) == OCI_MANIFEST]
        if len(manifests) != 1:
            raise SandboxError(f"Snapshot '{self.path}' holds {len(manifests)} images; expected exactly one.")
        return manifests[0]


def inspect(path: str) -> SnapshotInfo:
    archive = _Archive(path)
    try:
        descriptor = archive.manifest_descriptor()
        manifest = archive.json(_blob_path(descriptor["digest"]))
        config = archive.json(_blob_path(manifest["config"]["digest"]))
    finally:
        archive.close()
    user = str((config.get("config") or {}).get("User") or "") or ROOT_USER
    ref = str((descriptor.get("annotations") or {}).get("io.containerd.image.name") or "")
    if ref.startswith(LIBRARY_PREFIX):
        ref = ref[len(LIBRARY_PREFIX):]
    return SnapshotInfo(user=user, ref=ref, layers=len(manifest.get("layers", [])))


def rewrite(path: str, out_path: str, *, user: Optional[str], ref: str) -> SnapshotInfo:
    """
    Writes a copy of the archive whose config names `user` (unchanged when None) and
    whose tags name `ref` ("repository:tag"). Layers are copied as they are; the
    config, manifest and index are re-serialised with recomputed digests.
    """
    if ":" not in ref:
        raise SandboxError(f"Snapshot reference '{ref}' must be repository:tag.")
    archive = _Archive(path)
    descriptor = archive.manifest_descriptor()
    old_manifest_blob = _blob_path(descriptor["digest"])
    manifest = archive.json(old_manifest_blob)
    old_config_blob = _blob_path(manifest["config"]["digest"])
    config = archive.json(old_config_blob)
    if user is not None:
        config.setdefault("config", {})["User"] = user

    config_bytes = json.dumps(config, separators=(",", ":")).encode()
    manifest["config"].update(digest=_digest(config_bytes), size=len(config_bytes))
    manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode()
    descriptor.update(digest=_digest(manifest_bytes), size=len(manifest_bytes))
    annotations = descriptor.setdefault("annotations", {})
    annotations["io.containerd.image.name"] = LIBRARY_PREFIX + ref
    annotations["org.opencontainers.image.ref.name"] = ref.rsplit(":", 1)[1]
    index = archive.json("index.json")
    index["manifests"] = [descriptor]

    replaced: Dict[str, bytes] = {
        "index.json": json.dumps(index, separators=(",", ":")).encode(),
        _blob_path(manifest["config"]["digest"]): config_bytes,
        _blob_path(descriptor["digest"]): manifest_bytes,
    }
    if "manifest.json" in archive.members:
        docker_manifest = archive.json("manifest.json")
        for entry in docker_manifest:
            entry["Config"] = _blob_path(manifest["config"]["digest"])
            entry["RepoTags"] = [ref]
        replaced["manifest.json"] = json.dumps(docker_manifest, separators=(",", ":")).encode()

    dropped = {old_manifest_blob, old_config_blob, *replaced}
    try:
        with tarfile.open(out_path, "w") as out:
            for member in archive.tar.getmembers():
                if member.name in dropped:
                    continue
                out.addfile(member, archive.tar.extractfile(member) if member.isfile() else None)
            for name, data in replaced.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o644
                out.addfile(info, io.BytesIO(data))
    finally:
        archive.close()
    return inspect(out_path)
