from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.v37_hydramp_archive import inspect_hydramp_archive

V37_GENERATOR_LAUNCH_BINDING_SCHEMA = "v37.generator-live-launch-binding.1"


def _absolute_lexical(path: Path) -> Path:
    """Make a path absolute without resolving a link/reparse component away."""
    return Path(os.path.abspath(path))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _reject_reparse_chain(path: Path, *, root: Path, label: str) -> None:
    if not _is_within(path, root):
        raise ValueError(f"v37 {label} escapes its allowed root")
    current = path
    while True:
        if current.exists() or current.is_symlink():
            metadata = current.lstat()
            attributes = getattr(metadata, "st_file_attributes", 0)
            if current.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f"v37 {label} contains a symlink or reparse point")
        if current == root:
            break
        current = current.parent


def _workspace_release_root(workspace: Path, uri: object) -> Path:
    value = str(uri)
    prefix = "workspace-release://"
    if not value.startswith(prefix):
        raise ValueError("generator release is not workspace materialized")
    relative = value.removeprefix(prefix)
    parsed = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or parsed.is_absolute()
        or parsed.as_posix() != relative
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError("generator workspace release URI is unsafe")
    release_root = _absolute_lexical(workspace / "var/releases")
    release_path = _absolute_lexical(workspace / Path(*parsed.parts))
    _reject_reparse_chain(release_path, root=release_root, label="workspace release URI")
    return release_path


def iter_v37_runtime_tree_no_reparse(
    root: Path, *, allowed_root: Path, label: str
) -> list[Path]:
    """Return an exact runtime tree after rejecting every link/reparse point."""
    root = _absolute_lexical(root)
    allowed_root = _absolute_lexical(allowed_root)
    _reject_reparse_chain(root, root=allowed_root, label=label)
    if not root.is_dir():
        raise ValueError(f"v37 {label} is not a directory")
    observed: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            _reject_reparse_chain(path, root=root, label=label)
            observed.append(path)
            if path.is_dir():
                pending.append(path)
    return observed


def build_v37_generator_launch_binding(
    *,
    workspace: Path,
    runtime_index: Mapping[str, Any],
    entry: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind frozen identities to the exact local entities used by an activity."""
    workspace = workspace.resolve()
    _reject_reparse_chain(workspace, root=workspace, label="workspace")
    generator_id = str(entry["generator_id"])
    if manifest.get("generator_id") != generator_id:
        raise ValueError("generator launch binding identity mismatch")
    source_root = _absolute_lexical(
        _workspace_release_root(workspace, manifest["source_release"]["uri"])
        if str(manifest["source_release"]["uri"]).startswith("workspace-release://")
        else workspace
        / {
            "amp_designer": (
                "var/generator-sources/amp-designer-b554b1ac1507040d9d50356e037098e652ce4719"
            )
        }[generator_id]
    )
    model_root = _workspace_release_root(workspace, manifest["model_release"]["uri"])
    paths = {
        "python_path": str(
            _absolute_lexical(workspace / manifest["runtime"]["python_executable"])
        ),
        "adapter_path": str(
            _absolute_lexical(workspace / manifest["adapter"]["entrypoint"])
        ),
        "packages_lock_path": str(
            _absolute_lexical(
                workspace
                / "config/environments/v37_generator_runtimes"
                / f"{generator_id}.packages.lock.txt"
            )
        ),
        "source_root": str(source_root),
        "model_root": str(model_root),
    }
    for label in ("python_path", "adapter_path", "packages_lock_path"):
        _reject_reparse_chain(Path(paths[label]), root=workspace, label=label)
    launch: dict[str, Any] = {
        "schema_version": V37_GENERATOR_LAUNCH_BINDING_SCHEMA,
        "generator_id": generator_id,
        "runtime_index_sha256": runtime_index["runtime_index_sha256"],
        "runtime_manifest_sha256": manifest["runtime_manifest_sha256"],
        "expectation": dict(entry["expectation"]),
        "paths": paths,
        "device": "cpu" if generator_id == "amp_designer" else "cpu-only",
    }
    if generator_id == "hydramp":
        archive_path = _absolute_lexical(model_root / "models.zip")
        archive_contract = inspect_hydramp_archive(archive_path)
        frozen_archive_sha256 = next(
            item["sha256"]
            for item in manifest["model_release"]["files"]
            if item["path"] == "models.zip"
        )
        if archive_contract["archive_sha256"] != frozen_archive_sha256:
            raise ValueError("v37 HydrAMP archive differs from the frozen model release")
        launch["materialization"] = {
            "kind": "verified_zip_in_activity_workdir",
            "archive_path": str(archive_path),
            "decomposer_path": str(
                _absolute_lexical(model_root / "pca_decomposer.safe.npz")
            ),
            **archive_contract,
            # Upstream README freezes epoch 37 as the latest HydrAMP model.
            # The archive also contains Basic/PepCVAE checkpoints, but those
            # are different model families and must never be selected by
            # directory discovery or fallback.
            "model_subdirectory": "models/HydrAMP/37",
        }
    elif generator_id == "ampgan_v2":
        launch["arguments"] = {
            # AMPGAN v2 uses flat imports (``import avpdb``) and data paths
            # relative to its upstream ``ampgan`` working directory.
            "source_dir": str(_absolute_lexical(source_root / "ampgan")),
            "model_dir": str(_absolute_lexical(model_root / "gan_1606")),
        }
    elif generator_id == "amp_designer":
        launch["arguments"] = {
            "model_config_path": str(_absolute_lexical(model_root / "config.json")),
            "model_weights_path": str(
                _absolute_lexical(model_root / "pytorch_model.bin")
            ),
            "vocab_path": str(_absolute_lexical(model_root / "vocab.txt")),
        }
    else:
        raise ValueError(f"unknown v37 generator: {generator_id}")
    for label, raw in paths.items():
        path = Path(raw)
        if label.endswith("_root"):
            if not path.is_dir():
                raise ValueError(f"v37 {generator_id} {label} is not a directory")
        elif not path.is_file():
            raise ValueError(f"v37 {generator_id} {label} is not a file")
    for label, raw in {
        **launch.get("arguments", {}),
        **{
            key: value
            for key, value in launch.get("materialization", {}).items()
            if key.endswith("_path")
        },
    }.items():
        path = Path(raw)
        allowed_root = source_root if label == "source_dir" else model_root
        _reject_reparse_chain(path, root=allowed_root, label=label)
        if label.endswith("_dir"):
            if not path.is_dir():
                raise ValueError(f"v37 {generator_id} {label} is not a directory")
        elif not path.is_file():
            raise ValueError(f"v37 {generator_id} {label} is not a file")
    if sha256_file(Path(paths["python_path"])) != manifest["runtime"]["python_executable_sha256"]:
        raise ValueError(f"v37 {generator_id} Python bytes drifted")
    if sha256_file(Path(paths["adapter_path"])) != manifest["adapter"]["sha256"]:
        raise ValueError(f"v37 {generator_id} adapter bytes drifted")
    if (
        sha256_file(Path(paths["packages_lock_path"]))
        != manifest["runtime"]["packages_lock_sha256"]
    ):
        raise ValueError(f"v37 {generator_id} package lock bytes drifted")
    launch["launch_binding_sha256"] = sha256_json(launch)
    return launch


def verify_v37_generator_launch_binding(binding: Mapping[str, Any]) -> None:
    identity = {key: value for key, value in binding.items() if key != "launch_binding_sha256"}
    if binding.get("schema_version") != V37_GENERATOR_LAUNCH_BINDING_SCHEMA:
        raise ValueError("v37 generator launch binding schema drifted")
    if binding.get("launch_binding_sha256") != sha256_json(identity):
        raise ValueError("v37 generator launch binding self-hash drifted")
