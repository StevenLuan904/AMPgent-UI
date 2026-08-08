from __future__ import annotations

import io
import pickle
import zipfile
from pathlib import Path

import pytest

from pepagent.archive_audit import audit_zip_archive, scan_pickle_opcodes


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def test_archive_audit_is_content_addressed_and_reports_shell_source(tmp_path: Path) -> None:
    archive_path = tmp_path / "safe.zip"
    payload = pickle.dumps({"value": 1}, protocol=4)
    _write_zip(
        archive_path,
        {
            "hemopi2/model.sav": payload,
            "hemopi2/runner.py": b"import os\nos.system('forbidden')\n",
        },
    )
    first = audit_zip_archive(archive_path, required_root="hemopi2")
    second = audit_zip_archive(archive_path, required_root="hemopi2")
    assert first["inventory_sha256"] == second["inventory_sha256"]
    assert first["file_count"] == 2
    assert first["source_findings"] == [
        {
            "path": "hemopi2/runner.py",
            "finding": "shell_execution",
            "marker": "os.system(",
        }
    ]
    assert first["pickle_scans"][0]["path"] == "hemopi2/model.sav"


@pytest.mark.parametrize(
    "member",
    ["../escape.txt", "/absolute.txt", "other/file.txt"],
)
def test_archive_audit_rejects_unsafe_or_out_of_root_members(
    tmp_path: Path, member: str
) -> None:
    archive_path = tmp_path / "unsafe.zip"
    _write_zip(archive_path, {member: b"x"})
    with pytest.raises(ValueError):
        audit_zip_archive(archive_path, required_root="hemopi2")


def test_archive_audit_rejects_casefold_duplicate_paths(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("hemopi2/A.txt", b"a")
        archive.writestr("hemopi2/a.txt", b"b")
    with pytest.raises(ValueError, match="duplicate"):
        audit_zip_archive(archive_path, required_root="hemopi2")


def test_pickle_scan_does_not_execute_reduce_payload() -> None:
    touched = False

    class Payload:
        def __reduce__(self):
            return (setattr, (io.BytesIO(), "touched", True))

    data = pickle.dumps(Payload(), protocol=4)
    scan = scan_pickle_opcodes(data)
    assert scan["opcode_counts"]["REDUCE"] == 1
    assert touched is False


def test_pickle_scan_resolves_memoized_stack_globals() -> None:
    data = pickle.dumps({"items": {1, 2, 3}}, protocol=4)
    scan = scan_pickle_opcodes(data)
    assert "<unresolved-stack-global>" not in scan["global_references"]


def test_archive_audit_rejects_pickle_global_outside_allowlist(tmp_path: Path) -> None:
    archive_path = tmp_path / "untrusted.zip"
    _write_zip(
        archive_path,
        {"hemopi2/model.sav": pickle.dumps(PayloadForGlobalAudit(), protocol=4)},
    )
    with pytest.raises(ValueError, match="unexpected pickle globals"):
        audit_zip_archive(
            archive_path,
            required_root="hemopi2",
            allowed_pickle_globals=frozenset(),
        )


class PayloadForGlobalAudit:
    pass
