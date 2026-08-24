from __future__ import annotations

import asyncio
import json
import stat
import time
import zipfile
from pathlib import Path

import pytest

from pepagent.v37_generator_launch import (
    _workspace_release_root,
    build_v37_generator_launch_binding,
    verify_v37_generator_launch_binding,
)
from pepagent.v37_hydramp_archive import (
    inspect_hydramp_archive,
    materialize_hydramp_archive,
)
from pepagent.v37_runtime_execution import (
    V37LiveRuntimePaths,
    build_v37_live_launch_receipt,
)
from pepagent.v37_runtime_manifests import V37GeneratorRuntimeExpectation
from pepagent.workers.v37_activities import (
    _generator_command,
    _generator_request_payload,
    _materialize_hydramp_models,
    _materialize_hydramp_models_with_progress,
)

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "config/environments/v37_generator_runtimes"


def test_v38_generator_payload_is_one_score_all_cell() -> None:
    payload = _generator_request_payload(
        generator="amp_designer",
        seed=20270377,
        protocol="v38",
        raw_proposal_budget=100,
        device="cpu",
    )
    assert payload == {
        "schema_version": "v38.generator-request.1",
        "generator_id": "amp_designer",
        "seed": 20270377,
        "raw_proposal_budget": 100,
        "batch_size": 100,
        "batches": 1,
        "top_k": 10,
        "top_p": 1.0,
        "temperature": None,
        "decode_steps": 34,
        "device": "cpu",
    }


def test_v37_generator_payload_keeps_legacy_ten_batch_contract() -> None:
    payload = _generator_request_payload(
        generator="amp_designer",
        seed=20270377,
        protocol="v37",
        raw_proposal_budget=1000,
        device="cpu",
    )
    assert "schema_version" not in payload
    assert payload["batches"] == 10
    assert payload["raw_proposal_budget"] == 1000


def test_hydramp_materialization_emits_progress_while_archive_is_processed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    progress: list[str] = []

    def slow_materialization(_binding: dict, _work: Path) -> tuple[Path, dict]:
        time.sleep(0.05)
        return tmp_path / "models", {"destination_name": "models"}

    async def progress_writer() -> None:
        progress.append("heartbeat")

    monkeypatch.setattr(
        "pepagent.workers.v37_activities._materialize_hydramp_models",
        slow_materialization,
    )
    result = asyncio.run(
        _materialize_hydramp_models_with_progress(
            {},
            tmp_path,
            progress_writer=progress_writer,
            progress_interval_seconds=0.01,
        )
    )

    assert result[1]["destination_name"] == "models"
    assert progress


def test_hydramp_worker_materialization_reuses_content_addressed_cache(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "models.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("models/HydrAMP/37/weights.bin", b"frozen")
    expected = inspect_hydramp_archive(archive)
    binding = {
        "materialization": {
            **expected,
            "archive_path": str(archive),
            "model_subdirectory": "models/HydrAMP/37",
        }
    }
    cache_root = tmp_path / "cache"
    first_model, first_receipt = _materialize_hydramp_models(
        binding, tmp_path / "work-one", cache_root
    )
    second_model, second_receipt = _materialize_hydramp_models(
        binding, tmp_path / "work-two", cache_root
    )
    assert first_model == second_model
    assert first_receipt["persistent_cache"] is True
    assert first_receipt["cache_hit"] is False
    assert second_receipt["cache_hit"] is True


def test_actual_frozen_generator_launch_bindings_are_byte_exact(tmp_path: Path) -> None:
    index = json.loads((RUNTIME_ROOT / "runtime-index.json").read_text(encoding="utf-8"))
    for entry in index["entries"]:
        manifest = json.loads((ROOT / entry["manifest_path"]).read_text(encoding="utf-8"))
        binding = build_v37_generator_launch_binding(
            workspace=ROOT,
            runtime_index=index,
            entry=entry,
            manifest=manifest,
        )
        verify_v37_generator_launch_binding(binding)
        generator_id = entry["generator_id"]
        work = tmp_path / generator_id
        work.mkdir()
        request = work / "request.json"
        output = work / "output.json"
        request.write_text("{}", encoding="utf-8")
        materialized = (
            _materialize_hydramp_models(binding, work)
            if generator_id == "hydramp"
            else None
        )
        model_path = materialized[0] if materialized else None
        command = _generator_command(
            {"generator_id": generator_id},
            binding,
            request,
            output,
            hydramp_model_path=model_path,
        )
        paths = binding["paths"]
        receipt = build_v37_live_launch_receipt(
            manifest=manifest,
            expectation=V37GeneratorRuntimeExpectation(**entry["expectation"]),
            paths=V37LiveRuntimePaths(
                adapter_path=Path(paths["adapter_path"]),
                python_path=Path(paths["python_path"]),
                packages_lock_path=Path(paths["packages_lock_path"]),
                source_root=Path(paths["source_root"]),
                model_root=Path(paths["model_root"]),
            ),
            command=command,
            cwd=work,
            env={"PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert receipt["identity"]["generator_id"] == generator_id
        assert receipt["identity"]["runtime_manifest_sha256"] == (
            manifest["runtime_manifest_sha256"]
        )
        if generator_id == "amp_designer":
            assert binding["device"] == "cpu"


def test_workspace_release_uri_rejects_traversal(tmp_path: Path) -> None:
    (tmp_path / "var/releases").mkdir(parents=True)
    with pytest.raises(ValueError, match="unsafe"):
        _workspace_release_root(tmp_path, "workspace-release://var/releases/../escape")


@pytest.mark.parametrize("names", [["A/file.bin", "a/file.bin"], ["x", "x"]])
def test_hydramp_archive_rejects_duplicate_or_casefold_members(
    tmp_path: Path, names: list[str]
) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for index, name in enumerate(names):
            bundle.writestr(name, f"payload-{index}")
    with pytest.raises(ValueError, match="duplicate"):
        inspect_hydramp_archive(archive)


def test_hydramp_archive_rejects_traversal_and_symlink(tmp_path: Path) -> None:
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as bundle:
        bundle.writestr("../escape.bin", b"x")
    with pytest.raises(ValueError, match="unsafe"):
        inspect_hydramp_archive(traversal)

    symlink = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(symlink, "w") as bundle:
        bundle.writestr(info, "target")
    with pytest.raises(ValueError, match="unsafe"):
        inspect_hydramp_archive(symlink)


def test_hydramp_materialization_is_fresh_and_tree_bound(tmp_path: Path) -> None:
    archive = tmp_path / "models.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("models/HydrAMP/37/weights.bin", b"frozen")
    expected = inspect_hydramp_archive(archive)
    first, first_receipt = materialize_hydramp_archive(
        archive, work=tmp_path / "work", expected=expected
    )
    second, second_receipt = materialize_hydramp_archive(
        archive, work=tmp_path / "work", expected=expected
    )
    assert first != second
    assert first_receipt["extracted_tree_sha256"] == expected["extracted_tree_sha256"]
    assert second_receipt["materialization_receipt_sha256"] == (
        first_receipt["materialization_receipt_sha256"]
    )
