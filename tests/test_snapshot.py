import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest

from agy_sandbox import snapshot
from agy_sandbox.errors import SandboxError


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def oci_archive(path: str, *, user, ref="agy-sandbox-demo:recreate-1", layers=("layer-a", "layer-b")) -> dict:
    """A minimal OCI layout tar like `sbx template save --output` writes: index -> manifest -> config + layers."""
    blobs = {}
    layer_descs = []
    for payload in layers:
        data = payload.encode()
        blobs[digest(data)] = data
        layer_descs.append({"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": digest(data), "size": len(data)})
    config = {"architecture": "arm64", "os": "linux", "config": {"Env": ["A=1"], "WorkingDir": "/w"}, "history": [{"created_by": "x"}]}
    if user is not None:
        config["config"]["User"] = user
    config_b = json.dumps(config).encode()
    blobs[digest(config_b)] = config_b
    manifest = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": digest(config_b), "size": len(config_b)},
                "layers": layer_descs}
    manifest_b = json.dumps(manifest).encode()
    blobs[digest(manifest_b)] = manifest_b
    index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [{
        "mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": digest(manifest_b), "size": len(manifest_b),
        "annotations": {"io.containerd.image.name": "docker.io/library/" + ref, "org.opencontainers.image.ref.name": ref.split(":")[1]}}]}
    docker_manifest = [{"Config": "blobs/" + digest(config_b).replace(":", "/"), "RepoTags": [ref],
                        "Layers": ["blobs/" + d["digest"].replace(":", "/") for d in layer_descs]}]
    with tarfile.open(path, "w") as tar:
        def add(name, data):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        add("oci-layout", b'{"imageLayoutVersion":"1.0.0"}')
        add("index.json", json.dumps(index).encode())
        add("manifest.json", json.dumps(docker_manifest).encode())
        for d, data in blobs.items():
            add("blobs/" + d.replace(":", "/"), data)
    return {"layers": {d["digest"] for d in layer_descs}, "config": config}


def read_back(path: str):
    with tarfile.open(path) as tar:
        names = {m.name for m in tar.getmembers()}
        index = json.loads(tar.extractfile("index.json").read())
        desc = index["manifests"][0]
        manifest_b = tar.extractfile("blobs/" + desc["digest"].replace(":", "/")).read()
        manifest = json.loads(manifest_b)
        config_b = tar.extractfile("blobs/" + manifest["config"]["digest"].replace(":", "/")).read()
        docker = json.loads(tar.extractfile("manifest.json").read())
    return names, index, desc, manifest, manifest_b, json.loads(config_b), config_b, docker


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "snap.tar")
        self.out = os.path.join(self.tmp.name, "out.tar")

    def tearDown(self):
        self.tmp.cleanup()

    def test_inspect_reads_user_ref_and_layers_and_treats_unset_user_as_root(self):
        oci_archive(self.src, user="root")
        info = snapshot.inspect(self.src)
        self.assertEqual((info.user, info.ref, info.layers, info.starts_as_agent), ("root", "agy-sandbox-demo:recreate-1", 2, False))
        oci_archive(self.src, user=None)
        self.assertEqual(snapshot.inspect(self.src).user, "root")
        oci_archive(self.src, user="1000:1000")
        self.assertTrue(snapshot.inspect(self.src).starts_as_agent)
        oci_archive(self.src, user="agent")
        self.assertTrue(snapshot.inspect(self.src).starts_as_agent)

    def test_rewrite_changes_only_user_and_tags_and_keeps_every_layer_with_consistent_digests(self):
        made = oci_archive(self.src, user="root")
        info = snapshot.rewrite(self.src, self.out, user="agent", ref="agy-sandbox-demo-v2:recreate-1")
        self.assertEqual((info.user, info.ref, info.layers), ("agent", "agy-sandbox-demo-v2:recreate-1", 2))
        names, index, desc, manifest, manifest_b, config, config_b, docker = read_back(self.out)
        # the config differs from the original only in User
        expected = dict(made["config"])
        expected["config"] = dict(expected["config"], User="agent")
        self.assertEqual(config, expected)
        # digests and sizes are consistent, old config/manifest blobs are gone, layers untouched
        self.assertEqual(manifest["config"]["digest"], digest(config_b))
        self.assertEqual(manifest["config"]["size"], len(config_b))
        self.assertEqual(desc["digest"], digest(manifest_b))
        self.assertEqual(desc["size"], len(manifest_b))
        self.assertEqual({d["digest"] for d in manifest["layers"]}, made["layers"])
        for layer in made["layers"]:
            self.assertIn("blobs/" + layer.replace(":", "/"), names)
        self.assertEqual(len([n for n in names if n.startswith("blobs/sha256/")]), 4)   # 2 layers + config + manifest
        self.assertEqual(desc["annotations"]["io.containerd.image.name"], "docker.io/library/agy-sandbox-demo-v2:recreate-1")
        self.assertEqual(desc["annotations"]["org.opencontainers.image.ref.name"], "recreate-1")
        self.assertEqual(docker[0]["RepoTags"], ["agy-sandbox-demo-v2:recreate-1"])
        self.assertEqual(docker[0]["Config"], "blobs/" + manifest["config"]["digest"].replace(":", "/"))

    def test_rewrite_without_user_only_retags(self):
        oci_archive(self.src, user="agent")
        info = snapshot.rewrite(self.src, self.out, user=None, ref="other:tag")
        self.assertEqual((info.user, info.ref), ("agent", "other:tag"))

    def test_bad_inputs_are_sandbox_errors(self):
        oci_archive(self.src, user="root")
        with self.assertRaises(SandboxError):
            snapshot.rewrite(self.src, self.out, user="agent", ref="no-tag-here")
        with tarfile.open(self.src, "w") as tar:
            info = tarfile.TarInfo("oci-layout")
            info.size = 2
            tar.addfile(info, io.BytesIO(b"{}"))
        with self.assertRaises(SandboxError):
            snapshot.inspect(self.src)
