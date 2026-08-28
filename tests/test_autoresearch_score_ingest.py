from __future__ import annotations

import csv
import io
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pepagent.autoresearch_score_ingest import (
    FORMAL_SCORE_COLUMNS,
    GURUPRASAD_OOD_COLUMN,
    PRIMARY_IDENTITY_COLUMNS,
    RAW_OCCURRENCE_COLUMNS,
    validate_score_all_bundle,
    validate_score_source_map_receipt,
)
from pepagent.domain.enums import RunStatus
from pepagent.provenance.hashing import sha256_bytes, sha256_text
from pepagent.workers import autoresearch_activities as activity_module


def _bom_csv(rows: list[dict[str, str]], columns: tuple[str, ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def _primary_row(
    *, candidate_id: str, sequence: str, target_key: str, source: str, rank: int
) -> dict[str, str]:
    source_result = f"score/{source}"
    row = {
        name: ""
        for name in (
            *PRIMARY_IDENTITY_COLUMNS,
            *FORMAL_SCORE_COLUMNS,
            GURUPRASAD_OOD_COLUMN,
        )
    }
    row.update(
        {
            "candidate_id": candidate_id,
            "sequence": sequence,
            "sequence_sha256": sha256_text(sequence),
            "target_key": target_key,
            "generator_id": "PepMLM",
            "generator_seed": "17",
            "raw_rank": str(rank),
            "source_result": source_result,
            "source_result_sha256": sha256_text(source_result),
            "action_id": f"action-{candidate_id}",
            "action_kind": "de_novo",
            "action_seed": "17",
            "action_sha256": sha256_text(f"action-{candidate_id}"),
            "lineage": "generation-zero",
            "amp_read_log10_mic_um": "0.4",
            "llamp_log10_mic_um": "0.5",
            "macrel_amp_probability": "0.8",
            "toxinpred3_label": "Non-Toxin",
            "toxinpred3_hybrid_score": "0.1",
            "macrel_hemolysis_label": "low",
            "macrel_hemolysis_probability": "0.1",
            "net_charge_ph7_4": "3.0",
            "hydrophobic_ratio_modlamp": "0.4",
            "hydrophobic_moment_eisenberg": "0.5",
            "maximum_hydrophobic_run": "3",
            "guruprasad_instability_index": "22.0",
            "guruprasad_instability_ood": "true",
        }
    )
    return row


def _raw_row(primary: dict[str, str]) -> dict[str, str]:
    row = {name: "" for name in RAW_OCCURRENCE_COLUMNS}
    for name in (
        "action_id",
        "action_kind",
        "action_seed",
        "action_sha256",
        "candidate_id",
        "donor_candidate_id",
        "generator_id",
        "generator_seed",
        "lineage",
        "primary_parent_id",
        "raw_rank",
        "sequence",
        "sequence_sha256",
        "source_result",
        "source_result_sha256",
        "target_key",
    ):
        row[name] = primary[name]
    row.update(
        {
            "duplicate_within_expansion": "false",
            "proposal_mode": "de_novo",
            "sampling_attempt": "1",
            "sampling_seed": "17",
            "seed": "17",
            "source_action_plan": "plan.json",
            "source_action_plan_sha256": sha256_text("plan.json"),
            "valid_sequence": "true",
        }
    )
    return row


def _build_bundle(
    root: Path,
    *,
    manifest_file_count: int,
    include_receipt_in_manifest: bool = False,
    raw_identity_drift: bool = False,
    mix_pbp_source_results: bool = False,
) -> tuple[dict[str, Any], bytes, str]:
    primary_rows = [
        _primary_row(
            candidate_id="pbp-1",
            sequence="KRWLAKIRKL",
            target_key="PBP2a",
            source="pbp2a-r20.json",
            rank=1,
        ),
        _primary_row(
            candidate_id="pbp-2",
            sequence="KWRLAKIRKL",
            target_key="PBP2a",
            source="pbp2a-r21.json",
            rank=1,
        ),
        _primary_row(
            candidate_id="fgf-1",
            sequence="KRLWAKLIRK",
            target_key="FGF2",
            source="fgf2-score-input.json",
            rank=1,
        ),
    ]
    if mix_pbp_source_results:
        primary_rows[0]["source_result"] = primary_rows[1]["source_result"]
        primary_rows[0]["source_result_sha256"] = primary_rows[1][
            "source_result_sha256"
        ]
    primary_path = "score/all_scored.csv"
    strict_path = "score/strict.csv"
    raw_path = "score/raw_occurrence_audit.csv"
    score_receipt_path = "score/score.receipt.json"
    raw_rows = [_raw_row(row) for row in primary_rows]
    if raw_identity_drift:
        raw_rows[0]["generator_seed"] = "999"
    payloads = {
        primary_path: _bom_csv(
            primary_rows,
            (*PRIMARY_IDENTITY_COLUMNS, *FORMAL_SCORE_COLUMNS, GURUPRASAD_OOD_COLUMN),
        ),
        strict_path: _bom_csv(
            primary_rows,
            (*PRIMARY_IDENTITY_COLUMNS, *FORMAL_SCORE_COLUMNS, GURUPRASAD_OOD_COLUMN),
        ),
        raw_path: _bom_csv(raw_rows, RAW_OCCURRENCE_COLUMNS),
        score_receipt_path: b'{"status":"succeeded"}\n',
    }
    if include_receipt_in_manifest:
        payloads["bundle.receipt.json"] = b"manifest-placeholder"
    filler_index = 0
    while len(payloads) < manifest_file_count:
        path = f"evidence/filler-{filler_index:02d}.json"
        payloads[path] = json.dumps(
            {"filler": filler_index}, sort_keys=True
        ).encode("utf-8")
        filler_index += 1
    if len(payloads) != manifest_file_count:
        raise AssertionError("test manifest size is smaller than required evidence files")
    for relative_path, payload in payloads.items():
        target = root.joinpath(*relative_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    manifest = "".join(
        f"{sha256_bytes(payload)}  {path}\n"
        for path, payload in sorted(payloads.items())
    ).encode("utf-8")
    manifest_path = "MANIFEST.sha256"
    (root / manifest_path).write_bytes(manifest)
    receipt = {
        "schema_version": "ampgent.score-all-bundle.1",
        "status": "succeeded",
        "run_id": "external-score-run",
        "created_at": "2026-08-28T00:00:00Z",
        "storage_uri": "ssh://example.invalid/cas/bundle/",
        "content_address_key": sha256_bytes(payloads[primary_path]),
        "primary_result": {
            "path": primary_path,
            "sha256": sha256_bytes(payloads[primary_path]),
        },
        "strict_subset": {
            "path": strict_path,
            "sha256": sha256_bytes(payloads[strict_path]),
        },
        "raw_occurrence_audit": {
            "path": raw_path,
            "sha256": sha256_bytes(payloads[raw_path]),
        },
        "score_receipt": {
            "path": score_receipt_path,
            "sha256": sha256_bytes(payloads[score_receipt_path]),
        },
        "manifest": {
            "path": manifest_path,
            "sha256": sha256_bytes(manifest),
            "file_count": manifest_file_count,
        },
        "counts": {"raw": 3, "formal12": 3, "strict": 3, "ge2": 2, "three": 0},
        "source_splits": [
            {
                "source": "PBP2a_r20",
                "target_key": "PBP2a",
                "raw": 1,
                "strict": 1,
                "activity_support_ge_2": 1,
                "activity_support_3": 0,
                "new_unique": 1,
            },
            {
                "source": "PBP2a_r21",
                "target_key": "PBP2a",
                "raw": 1,
                "strict": 1,
                "activity_support_ge_2": 1,
                "activity_support_3": 0,
                "new_unique": 1,
            },
            {
                "source": "FGF2_v6",
                "target_key": "FGF2",
                "raw": 1,
                "strict": 1,
                "activity_support_ge_2": 0,
                "activity_support_3": 0,
                "new_unique": 1,
            },
        ],
        "family_analysis": {},
        "runtime": {
            "adapter_commit": "commit",
            "adapter_sha": "a" * 64,
            "scorer_sha": "b" * 64,
            "registry_sha": "c" * 64,
            "python_sha": "d" * 64,
        },
        "warnings": [],
    }
    receipt_bytes = json.dumps(receipt, sort_keys=True).encode("utf-8")
    (root / "bundle.receipt.json").write_bytes(receipt_bytes)
    return receipt, receipt_bytes, sha256_bytes(receipt_bytes)


def _build_source_map(
    root: Path,
    *,
    bundle_receipt_sha256: str,
) -> tuple[dict[str, Any], bytes, str]:
    source_paths = {
        "PBP2a_r20": "score/pbp2a-r20.json",
        "PBP2a_r21": "score/pbp2a-r21.json",
        "FGF2_v6": "score/fgf2-score-input.json",
    }
    receipt = {
        "schema_version": "ampgent.score-source-map.v1",
        "status": "complete",
        "created_at": "2026-08-28T00:01:00Z",
        "runs": [
            {
                "run_id": "external-score-run",
                "bundle_receipt_sha256": bundle_receipt_sha256,
                "mappings": [
                    {
                        "source_label": label,
                        "source_result_basename": Path(path).name,
                        "source_result_sha256": sha256_text(path),
                    }
                    for label, path in source_paths.items()
                ],
            }
        ],
    }
    payload = json.dumps(receipt, sort_keys=True).encode("utf-8")
    (root / "score_source_map.receipt.json").write_bytes(payload)
    return receipt, payload, sha256_bytes(payload)


def _validated_source_mappings(
    root: Path,
    *,
    bundle_receipt_sha256: str,
) -> dict[str, tuple[str, str]]:
    receipt, payload, digest = _build_source_map(
        root,
        bundle_receipt_sha256=bundle_receipt_sha256,
    )
    return validate_score_source_map_receipt(
        receipt=receipt,
        receipt_sha256=digest,
        receipt_bytes=payload,
        source_run_id="external-score-run",
        bundle_receipt_sha256=bundle_receipt_sha256,
    ).source_result_mappings


def _reader(root: Path) -> Any:
    return lambda relative_path: root.joinpath(*relative_path.split("/")).read_bytes()


@pytest.mark.parametrize("manifest_file_count", [32, 33])
def test_score_bundle_validator_accepts_variable_complete_manifests_and_filters_target(
    tmp_path: Path, manifest_file_count: int
) -> None:
    receipt, receipt_bytes, receipt_sha = _build_bundle(
        tmp_path, manifest_file_count=manifest_file_count
    )

    validated = validate_score_all_bundle(
        bundle_receipt=receipt,
        bundle_receipt_sha256=receipt_sha,
        bundle_receipt_bytes=receipt_bytes,
        bundle_receipt_relative_path="bundle.receipt.json",
        target_key="PBP2a",
        source_result_mappings=_validated_source_mappings(
            tmp_path, bundle_receipt_sha256=receipt_sha
        ),
        read_bytes=_reader(tmp_path),
    )

    assert len(validated.all_manifest_files) == manifest_file_count
    assert [row["candidate_id"] for row in validated.primary_rows] == [
        "pbp-1",
        "pbp-2",
    ]
    assert [row["candidate_id"] for row in validated.raw_rows] == [
        "pbp-1",
        "pbp-2",
    ]
    assert validated.strict_sequence_sha256s == tuple(
        sorted((sha256_text("KRWLAKIRKL"), sha256_text("KWRLAKIRKL")))
    )


def test_score_bundle_validator_fails_closed_on_tampered_member(tmp_path: Path) -> None:
    receipt, receipt_bytes, receipt_sha = _build_bundle(
        tmp_path, manifest_file_count=32
    )
    (tmp_path / "score" / "all_scored.csv").write_bytes(b"tampered")

    with pytest.raises(OSError, match="file SHA-256 mismatch"):
        validate_score_all_bundle(
            bundle_receipt=receipt,
            bundle_receipt_sha256=receipt_sha,
            bundle_receipt_bytes=receipt_bytes,
            bundle_receipt_relative_path="bundle.receipt.json",
            target_key="PBP2a",
            source_result_mappings=_validated_source_mappings(
                tmp_path, bundle_receipt_sha256=receipt_sha
            ),
            read_bytes=_reader(tmp_path),
        )


def test_score_bundle_validator_rejects_receipt_inside_manifest(tmp_path: Path) -> None:
    receipt, receipt_bytes, receipt_sha = _build_bundle(
        tmp_path,
        manifest_file_count=32,
        include_receipt_in_manifest=True,
    )

    with pytest.raises(ValueError, match="must not contain its bundle receipt"):
        validate_score_all_bundle(
            bundle_receipt=receipt,
            bundle_receipt_sha256=receipt_sha,
            bundle_receipt_bytes=receipt_bytes,
            bundle_receipt_relative_path="bundle.receipt.json",
            target_key="PBP2a",
            source_result_mappings=_validated_source_mappings(
                tmp_path, bundle_receipt_sha256=receipt_sha
            ),
            read_bytes=_reader(tmp_path),
        )


def test_score_bundle_validator_rejects_raw_primary_identity_drift(
    tmp_path: Path,
) -> None:
    receipt, receipt_bytes, receipt_sha = _build_bundle(
        tmp_path,
        manifest_file_count=33,
        raw_identity_drift=True,
    )

    with pytest.raises(ValueError, match="identity differs"):
        validate_score_all_bundle(
            bundle_receipt=receipt,
            bundle_receipt_sha256=receipt_sha,
            bundle_receipt_bytes=receipt_bytes,
            bundle_receipt_relative_path="bundle.receipt.json",
            target_key="PBP2a",
            source_result_mappings=_validated_source_mappings(
                tmp_path, bundle_receipt_sha256=receipt_sha
            ),
            read_bytes=_reader(tmp_path),
        )


def test_score_bundle_validator_does_not_mix_pbp2a_r20_and_r21(
    tmp_path: Path,
) -> None:
    receipt, receipt_bytes, receipt_sha = _build_bundle(
        tmp_path,
        manifest_file_count=33,
        mix_pbp_source_results=True,
    )

    with pytest.raises(ValueError, match="source split raw count"):
        validate_score_all_bundle(
            bundle_receipt=receipt,
            bundle_receipt_sha256=receipt_sha,
            bundle_receipt_bytes=receipt_bytes,
            bundle_receipt_relative_path="bundle.receipt.json",
            target_key="PBP2a",
            source_result_mappings=_validated_source_mappings(
                tmp_path, bundle_receipt_sha256=receipt_sha
            ),
            read_bytes=_reader(tmp_path),
        )


class _ActivitySession:
    def __init__(self, run_id: uuid.UUID) -> None:
        self.run_id = run_id
        self.scalar_values = [2, 2, 24]

    async def __aenter__(self) -> _ActivitySession:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def begin(self) -> _ActivitySession:
        return self

    async def get(self, _model: Any, identity: uuid.UUID) -> Any:
        if identity == self.run_id:
            return SimpleNamespace(status=RunStatus.RUNNING)
        return None

    async def scalar(self, _query: Any) -> int:
        return self.scalar_values.pop(0)


class _ActivityRepository:
    instances: list[_ActivityRepository] = []

    def __init__(self, _session: _ActivitySession) -> None:
        self.candidates: dict[str, Any] = {}
        self.occurrences: list[dict[str, Any]] = []
        self.evaluations: list[dict[str, Any]] = []
        self.__class__.instances.append(self)

    async def record_completed_tool_call(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(id=uuid.uuid4())

    async def add_candidate(
        self, _run_id: uuid.UUID, sequence: str, **kwargs: Any
    ) -> Any:
        digest = sha256_text(sequence)
        candidate = SimpleNamespace(
            id=uuid.uuid4(),
            sequence_sha256=digest,
            generation=kwargs["generation"],
            metadata_json=kwargs["metadata"],
        )
        self.candidates[digest] = candidate
        return candidate

    async def record_candidate_occurrence(self, **kwargs: Any) -> None:
        self.occurrences.append(kwargs)

    async def record_evaluation(self, *args: Any, **kwargs: Any) -> None:
        self.evaluations.append({"args": args, "kwargs": kwargs})


@pytest.mark.asyncio
async def test_score_bundle_activity_persists_all_raw_and_candidate_times_twelve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _receipt, _receipt_bytes, receipt_sha = _build_bundle(
        tmp_path, manifest_file_count=33
    )
    _map_receipt, _map_bytes, source_map_sha = _build_source_map(
        tmp_path, bundle_receipt_sha256=receipt_sha
    )
    run_id = uuid.uuid4()
    session = _ActivitySession(run_id)
    _ActivityRepository.instances.clear()
    monkeypatch.setattr(activity_module, "SessionFactory", lambda: session)
    monkeypatch.setattr(activity_module, "ExperimentRepository", _ActivityRepository)
    monkeypatch.setattr(
        activity_module.activity, "info", lambda: SimpleNamespace(attempt=1)
    )

    async def fake_register_artifact(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(activity_module, "_register_artifact", fake_register_artifact)

    result = await activity_module.persist_autoresearch_score_all_bundle(
        {
            "run_id": str(run_id),
            "target_key": "PBP2a",
            "bundle_cache_root": str(tmp_path),
            "bundle_receipt_path": "bundle.receipt.json",
            "bundle_receipt_sha256": receipt_sha,
            "source_map_receipt_path": "score_source_map.receipt.json",
            "source_map_receipt_sha256": source_map_sha,
            "source_map_storage_uri": (
                f"ssh://example.invalid/cas/{source_map_sha}/"
                "score_source_map.receipt.json"
            ),
            "control_environment_sha256": "e" * 64,
        }
    )

    repository = _ActivityRepository.instances[-1]
    assert result["candidate_count"] == 2
    assert result["occurrence_count"] == 2
    assert result["evaluation_count"] == 24
    assert len(repository.occurrences) == 2
    assert {
        row["metadata"]["source_candidate_id"] for row in repository.occurrences
    } == {"pbp-1", "pbp-2"}
    assert {item["args"][2] for item in repository.evaluations} == set(
        FORMAL_SCORE_COLUMNS
    )
    instability_rows = [
        item
        for item in repository.evaluations
        if item["args"][2] == "guruprasad_instability_index"
    ]
    assert all(item["kwargs"]["out_of_domain"] for item in instability_rows)
    assert result["strict_subset_used_as_raw"] is False
