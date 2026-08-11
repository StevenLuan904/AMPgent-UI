from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_json
from pepagent.v34_runtime import (
    augment_runtime_with_conda_metadata,
    probe_python_runtime,
    verify_python_runtime_manifest,
)


def test_runtime_manifest_is_path_free_and_verified() -> None:
    manifest = probe_python_runtime(
        python_executable=Path(sys.executable),
        role="shadow-test",
        source_manifest_sha256="a" * 64,
        requirements=(("pytest>=8,<10", "pytest"),),
    )

    assert str(Path(sys.executable).parent) not in str(manifest)
    verified = verify_python_runtime_manifest(
        manifest,
        expected_role="shadow-test",
        expected_source_manifest_sha256="a" * 64,
        python_specifier=">=3.11,<3.12",
    )
    assert verified["verified"] is True


def test_runtime_manifest_fails_closed_on_missing_or_unimportable_package() -> None:
    manifest = probe_python_runtime(
        python_executable=Path(sys.executable),
        role="shadow-test",
        source_manifest_sha256="b" * 64,
        requirements=(("definitely-missing-v34-package==1.0", "missing_v34_module"),),
    )

    with pytest.raises(ValueError, match="requirement is not satisfied"):
        verify_python_runtime_manifest(
            manifest,
            expected_role="shadow-test",
            expected_source_manifest_sha256="b" * 64,
            python_specifier=">=3.11,<3.12",
        )


def test_conda_metadata_supplies_non_wheel_version_without_hiding_import_failure(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / "conda-meta"
    metadata.mkdir()
    record = {
        "name": "pymol-open-source",
        "version": "3.1.0",
        "build": "py311_0",
        "subdir": "win-64",
    }
    (metadata / "pymol-open-source-3.1.0.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    manifest = {
        "schema_version": "1.0",
        "role": "renderer",
        "source_manifest_sha256": "c" * 64,
        "python_executable_sha256": "d" * 64,
        "python_version": "3.11.15",
        "packages": [
            {
                "requirement": "pymol-open-source==3.1.0",
                "distribution": "pymol-open-source",
                "module": "pymol",
                "version": None,
                "import_ok": True,
                "module_file_sha256": "e" * 64,
            }
        ],
    }
    manifest["runtime_manifest_sha256"] = sha256_json(manifest)

    enriched = augment_runtime_with_conda_metadata(manifest, conda_prefix=tmp_path)
    verified = verify_python_runtime_manifest(
        enriched,
        expected_role="renderer",
        expected_source_manifest_sha256="c" * 64,
        python_specifier=">=3.11,<3.12",
    )

    assert enriched["packages"][0]["version_source"] == "conda-meta"
    assert verified["verified"] is True
