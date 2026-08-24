from __future__ import annotations

import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import pepagent.v37_hydramp_archive as archive_module
from pepagent.v37_generator_launch import iter_v37_runtime_tree_no_reparse
from pepagent.v37_hydramp_archive import (
    HYDRAMP_ARCHIVE_RESOURCE_LIMITS,
    cleanup_hydramp_materialization,
    inspect_hydramp_archive,
    materialize_hydramp_archive,
    materialize_hydramp_archive_cached,
    verify_hydramp_materialization,
)


def _archive(path: Path, members: dict[str, bytes], *, compressed: bool = False) -> Path:
    compression = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w", compression=compression) as bundle:
        for name, payload in members.items():
            bundle.writestr(name, payload)
    return path


@pytest.mark.parametrize(
    "name",
    [
        "models/CON/file.bin",
        "models/nul.txt",
        "models/trailing./file.bin",
        "models/trailing /file.bin",
        "models/control\x1f/file.bin",
        "models/question?/file.bin",
    ],
)
def test_hydramp_archive_rejects_windows_aliased_names(
    tmp_path: Path, name: str
) -> None:
    path = _archive(tmp_path / "unsafe.zip", {name: b"x"})
    with pytest.raises(ValueError, match="Windows|control"):
        inspect_hydramp_archive(path)


def test_hydramp_archive_rejects_non_nfc_member_names(tmp_path: Path) -> None:
    decomposed = unicodedata.normalize("NFD", "café")
    assert decomposed != unicodedata.normalize("NFC", decomposed)
    path = _archive(tmp_path / "unsafe.zip", {f"models/{decomposed}.bin": b"x"})
    with pytest.raises(ValueError, match="NFC"):
        inspect_hydramp_archive(path)


def test_hydramp_archive_enforces_member_and_streamed_byte_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_members = _archive(tmp_path / "members.zip", {"a": b"a", "b": b"b"})
    monkeypatch.setattr(archive_module, "HYDRAMP_ARCHIVE_MAX_MEMBERS", 1)
    with pytest.raises(ValueError, match="member-count"):
        inspect_hydramp_archive(two_members)

    monkeypatch.setattr(archive_module, "HYDRAMP_ARCHIVE_MAX_MEMBERS", 10)
    monkeypatch.setattr(archive_module, "HYDRAMP_ARCHIVE_MAX_FILE_BYTES", 3)
    oversized = _archive(tmp_path / "oversized.zip", {"large.bin": b"four"})
    with pytest.raises(ValueError, match="size limit"):
        inspect_hydramp_archive(oversized)

    monkeypatch.setattr(archive_module, "HYDRAMP_ARCHIVE_MAX_FILE_BYTES", 10)
    monkeypatch.setattr(archive_module, "HYDRAMP_ARCHIVE_MAX_UNCOMPRESSED_BYTES", 3)
    total = _archive(tmp_path / "total.zip", {"a": b"aa", "b": b"bb"})
    with pytest.raises(ValueError, match="uncompressed-byte"):
        inspect_hydramp_archive(total)


def test_hydramp_archive_rejects_extreme_compression_ratio(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "bomb.zip",
        {"repeated.bin": b"x" * (1024 * 1024)},
        compressed=True,
    )
    with pytest.raises(ValueError, match="compression-ratio"):
        inspect_hydramp_archive(path)


def test_hydramp_inspection_detects_archive_change_on_same_open_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _archive(tmp_path / "models.zip", {"model.bin": b"frozen"})
    identities = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(
        archive_module, "_sha256_open_file", lambda _source: next(identities)
    )
    with pytest.raises(ValueError, match="changed while being inspected"):
        inspect_hydramp_archive(path)


def test_failed_materialization_cleans_partial_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _archive(tmp_path / "models.zip", {"models/HydrAMP/37/model.bin": b"x"})
    expected = inspect_hydramp_archive(path)
    identities = iter([expected["archive_sha256"], "0" * 64])
    monkeypatch.setattr(
        archive_module, "_sha256_open_file", lambda _source: next(identities)
    )
    work = tmp_path / "work"
    with pytest.raises(ValueError, match="changed while being extracted"):
        materialize_hydramp_archive(path, work=work, expected=expected)
    assert list(work.iterdir()) == []


def test_materialization_receipt_freezes_limits_and_cleanup_is_scoped(
    tmp_path: Path,
) -> None:
    path = _archive(tmp_path / "models.zip", {"models/HydrAMP/37/model.bin": b"x"})
    expected = inspect_hydramp_archive(path)
    work = tmp_path / "work"
    destination, receipt = materialize_hydramp_archive(
        path, work=work, expected=expected
    )
    assert receipt["archive_resource_limits"] == HYDRAMP_ARCHIVE_RESOURCE_LIMITS
    cleanup_hydramp_materialization(destination, work=work)
    assert not destination.exists()
    with pytest.raises(ValueError, match="direct materialization child"):
        cleanup_hydramp_materialization(tmp_path, work=work)


def test_content_addressed_cache_reuses_and_reverifies_frozen_tree(tmp_path: Path) -> None:
    path = _archive(tmp_path / "models.zip", {"models/HydrAMP/37/model.bin": b"x"})
    expected = inspect_hydramp_archive(path)
    cache_root = tmp_path / "cache"
    first, first_receipt = materialize_hydramp_archive_cached(
        path, cache_root=cache_root, expected=expected
    )
    second, second_receipt = materialize_hydramp_archive_cached(
        path, cache_root=cache_root, expected=expected
    )
    assert first == second
    assert first_receipt["cache_hit"] is False
    assert second_receipt["cache_hit"] is True
    assert second_receipt["extracted_tree_sha256"] == expected["extracted_tree_sha256"]
    (second / "models" / "HydrAMP" / "37" / "model.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="byte count|tree drifted"):
        verify_hydramp_materialization(
            second, cache_root=cache_root, expected=expected
        )


def test_content_addressed_cache_serializes_cold_publishers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _archive(tmp_path / "models.zip", {"models/HydrAMP/37/model.bin": b"x"})
    expected = inspect_hydramp_archive(path)
    cache_root = tmp_path / "cache"
    real_materialize = archive_module.materialize_hydramp_archive
    calls: list[int] = []

    def counted_materialize(*args, **kwargs):
        calls.append(1)
        return real_materialize(*args, **kwargs)

    monkeypatch.setattr(
        archive_module, "materialize_hydramp_archive", counted_materialize
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: materialize_hydramp_archive_cached(
                    path, cache_root=cache_root, expected=expected
                ),
                range(2),
            )
        )
    assert len(calls) == 1
    assert results[0][0] == results[1][0]
    assert sorted(item[1]["cache_hit"] for item in results) == [False, True]


def test_materialization_rejects_symlink_work_root(tmp_path: Path) -> None:
    path = _archive(tmp_path / "models.zip", {"models/HydrAMP/37/model.bin": b"x"})
    expected = inspect_hydramp_archive(path)
    real_work = tmp_path / "real-work"
    real_work.mkdir()
    linked_work = tmp_path / "linked-work"
    try:
        linked_work.symlink_to(real_work, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"host cannot create directory symlinks: {exc}")
    with pytest.raises(ValueError, match="symlink or reparse"):
        materialize_hydramp_archive(path, work=linked_work, expected=expected)


def test_runtime_inventory_rejects_descendant_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = workspace / "runtime"
    runtime.mkdir(parents=True)
    external = tmp_path / "external.bin"
    external.write_bytes(b"same bytes")
    link = runtime / "model.bin"
    try:
        link.symlink_to(external)
    except OSError as exc:
        pytest.skip(f"host cannot create file symlinks: {exc}")
    with pytest.raises(ValueError, match="symlink or reparse"):
        iter_v37_runtime_tree_no_reparse(
            runtime, allowed_root=workspace, label="test runtime"
        )
