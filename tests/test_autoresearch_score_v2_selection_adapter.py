from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest

from pepagent.autoresearch_score_ingest import (
    FORMAL_SCORE_COLUMNS,
    GURUPRASAD_OOD_COLUMN,
    PRIMARY_IDENTITY_COLUMNS,
    RAW_OCCURRENCE_REQUIRED_COLUMNS,
    validate_score_all_bundle,
    validate_score_source_map_receipt,
)
from pepagent.autoresearch_score_v2_selection_adapter import (
    STRUCTURE_EXCLUSIONS_SCHEMA_VERSION,
    adapt_v2_selection_to_v1_bundle,
)
from pepagent.provenance.hashing import sha256_bytes, sha256_text


def _ordered_union(*groups: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for group in groups:
        for name in group:
            if name not in result:
                result.append(name)
    return tuple(result)


SOURCE_COLUMNS = _ordered_union(
    PRIMARY_IDENTITY_COLUMNS,
    FORMAL_SCORE_COLUMNS,
    (GURUPRASAD_OOD_COLUMN,),
    RAW_OCCURRENCE_REQUIRED_COLUMNS,
    (
        "activity_model_support_count",
        "display_eligible",
        "family_clustering_scope",
        "family_key_80_80",
        "formal_metric_count",
        "formal_metrics_complete",
        "instability_lt_50",
        "safety_labels_pass",
    ),
)


def _csv_bytes(
    rows: list[dict[str, str]], fields: tuple[str, ...], *, bom: bool = False
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    payload = output.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + payload) if bom else payload


def _row(
    *,
    candidate_id: str,
    sequence: str,
    target: str,
    family: str,
    support: int,
    source_name: str,
    instability_ood: bool = False,
) -> dict[str, str]:
    row = {name: "" for name in SOURCE_COLUMNS}
    source_result = rf"E:\score-source\{source_name}"
    action_id = f"action-{candidate_id}"
    row.update(
        {
            "candidate_id": candidate_id,
            "sequence": sequence,
            "sequence_sha256": sha256_text(sequence),
            "target_key": target,
            "generator_id": "pepmlm-target-conditional",
            "generator_seed": "42",
            "raw_rank": "1",
            "source_result": source_result,
            "source_result_sha256": sha256_text(source_result),
            "action_id": action_id,
            "action_kind": "de_novo",
            "action_seed": "42",
            "action_sha256": sha256_text(action_id),
            "primary_parent_id": "",
            "donor_candidate_id": "",
            "lineage": "generation-zero",
            "amp_read_log10_mic_um": "0.40",
            "llamp_log10_mic_um": "0.50",
            "macrel_amp_probability": "0.80",
            "toxinpred3_label": "Non-Toxin",
            "toxinpred3_hybrid_score": "0.10",
            "macrel_hemolysis_label": "low",
            "macrel_hemolysis_probability": "0.10",
            "net_charge_ph7_4": "3.0",
            "hydrophobic_ratio_modlamp": "0.40",
            "hydrophobic_moment_eisenberg": "0.50",
            "maximum_hydrophobic_run": "3",
            "guruprasad_instability_index": "22.0",
            "guruprasad_instability_ood": str(instability_ood),
            "duplicate_within_expansion": "False",
            "proposal_mode": "de_novo",
            "sampling_attempt": "1",
            "sampling_seed": "42",
            "seed": "42",
            "source_action_plan": "action-plan.json",
            "source_action_plan_sha256": sha256_text("action-plan.json"),
            "valid_sequence": "True",
            "activity_model_support_count": str(support),
            "display_eligible": "True",
            "family_clustering_scope": "global_strict_library_80_identity_80_coverage",
            "family_key_80_80": family,
            "formal_metric_count": "12",
            "formal_metrics_complete": "True",
            "instability_lt_50": "True",
            "safety_labels_pass": "True",
        }
    )
    return row


def _fixture_files(tmp_path: Path) -> dict[str, Any]:
    rows = [
        _row(
            candidate_id="acea-1",
            sequence="KRLWAKLIRKRLWAKLIRKL",
            target="acea",
            family="seqfam80_family_a",
            support=3,
            source_name="acea.json",
            instability_ood=True,
        ),
        _row(
            candidate_id="acea-2",
            sequence="RWKLAKIRKLWRAKILKRLW",
            target="acea",
            family="seqfam80_family_b",
            support=1,
            source_name="acea.json",
        ),
        _row(
            candidate_id="angpt1-1",
            sequence="KLAKIRRLWAKLIRKRLWAK",
            target="angpt1",
            family="seqfam80_family_c",
            support=2,
            source_name="angpt1.json",
        ),
    ]
    source_path = tmp_path / "strict_library_global.csv"
    source_payload = _csv_bytes(rows, SOURCE_COLUMNS)
    source_path.write_bytes(source_payload)
    source_sha = sha256_bytes(source_payload)
    selection_fields = (*SOURCE_COLUMNS, "v9_dry_rank", "v9_dry_lane")
    target_rank = {"acea": 0, "angpt1": 0}
    selected: list[dict[str, str]] = []
    for row in rows:
        target_rank[row["target_key"]] += 1
        selected.append(
            {
                **row,
                "v9_dry_rank": str(target_rank[row["target_key"]]),
                "v9_dry_lane": (
                    "consensus_support_ge_2"
                    if int(row["activity_model_support_count"]) >= 2
                    else "supplemental_safe_ood_qualified"
                ),
            }
        )
    selection_path = tmp_path / "selection.csv"
    selection_path.write_bytes(_csv_bytes(selected, selection_fields))
    manifest_path = tmp_path / "MANIFEST.sha256"
    manifest_payload = f"{source_sha}  library/strict_library_global.csv\n".encode()
    manifest_path.write_bytes(manifest_payload)
    receipt = {
        "schema_version": "ampgent.autoresearch-scoreall-bundle.v2",
        "status": "succeeded",
        "run_id": "score-all-v2-source",
        "created_at": "2026-08-28T21:01:45Z",
        "storage_uri": "ssh://example.invalid/artifacts/score-all/source/",
        "content_address_key": "a" * 64,
        "global_strict_library": {
            "path": "library/strict_library_global.csv",
            "sha256": source_sha,
            "bytes": len(source_payload),
        },
        "manifest": {
            "path": "MANIFEST.sha256",
            "sha256": sha256_bytes(manifest_payload),
            "file_count": 1,
        },
        "runtime": {"registry_sha256": "b" * 64},
    }
    receipt_payload = json.dumps(receipt, sort_keys=True).encode()
    receipt_path = tmp_path / "bundle.receipt.json"
    receipt_path.write_bytes(receipt_payload)
    return {
        "rows": rows,
        "selection_rows": selected,
        "selection_fields": selection_fields,
        "source_path": source_path,
        "selection_path": selection_path,
        "manifest_path": manifest_path,
        "receipt_path": receipt_path,
        "receipt_sha": sha256_bytes(receipt_payload),
        "source_sha": source_sha,
    }


def _exclusions(*, sequences: list[str] | None = None, families: list[str] | None = None) -> dict:
    return {
        "schema_version": STRUCTURE_EXCLUSIONS_SCHEMA_VERSION,
        "status": "complete",
        "sequence_sha256s": sequences or [],
        "family_keys": families or [],
    }


def _adapt(
    fixture: dict[str, Any],
    *,
    output_dir: Path,
    expected_counts: dict[str, int] | None = None,
    exclusions: dict[str, Any] | None = None,
):
    return adapt_v2_selection_to_v1_bundle(
        source_bundle_receipt_path=fixture["receipt_path"],
        source_manifest_path=fixture["manifest_path"],
        source_strict_library_path=fixture["source_path"],
        selection_csv_path=fixture["selection_path"],
        structure_exclusions=exclusions or _exclusions(),
        expected_target_counts=expected_counts or {"acea": 2, "angpt1": 1},
        expected_source_bundle_receipt_sha256=fixture["receipt_sha"],
        expected_source_strict_library_sha256=fixture["source_sha"],
        output_dir=output_dir,
        run_id="v9-explicit-schema-adapter-test",
        created_at="2026-08-29T00:00:00Z",
        storage_uri_prefix="ssh://example.invalid/artifacts/autoresearch-v9-seeds/",
        adapter_revision="test-revision",
    )


def test_adapter_preserves_rows_and_builds_variable_count_v1_import_bundle(
    tmp_path: Path,
) -> None:
    fixture = _fixture_files(tmp_path)
    output_dir = tmp_path / "adapted"
    result = _adapt(fixture, output_dir=output_dir)

    assert result.target_counts == {"acea": 2, "angpt1": 1}
    assert result.formal_evaluation_count == 3 * 12
    assert result.adapter_receipt["preservation"] == {
        "source_columns": list(SOURCE_COLUMNS),
        "selection_metadata_columns": ["v9_dry_rank", "v9_dry_lane"],
        "output_columns": [*SOURCE_COLUMNS, "v9_dry_rank", "v9_dry_lane"],
        "target_key_preserved": True,
        "source_result_preserved": True,
        "family_identity_preserved": True,
        "formal_12_values_preserved_byte_for_byte": True,
        "scientific_metrics_recomputed": False,
    }
    output_rows = list(
        csv.DictReader(
            io.StringIO(
                (output_dir / "score" / "all_scored_audit.csv")
                .read_text(encoding="utf-8-sig")
            )
        )
    )
    source_by_sha = {row["sequence_sha256"]: row for row in fixture["rows"]}
    assert any(row[GURUPRASAD_OOD_COLUMN] == "True" for row in output_rows)
    for row in output_rows:
        source = source_by_sha[row["sequence_sha256"]]
        assert all(row[name] == source[name] for name in SOURCE_COLUMNS)
        assert row["v9_dry_lane"]
        assert row["v9_dry_rank"]

    receipt_bytes = (output_dir / "bundle.receipt.json").read_bytes()
    source_map_bytes = (output_dir / "score_source_map.receipt.json").read_bytes()
    source_map = json.loads(source_map_bytes)
    validated_map = validate_score_source_map_receipt(
        receipt=source_map,
        receipt_sha256=sha256_bytes(source_map_bytes),
        receipt_bytes=source_map_bytes,
        source_run_id="v9-explicit-schema-adapter-test",
        bundle_receipt_sha256=sha256_bytes(receipt_bytes),
    )
    for target, expected in result.target_counts.items():
        validated = validate_score_all_bundle(
            bundle_receipt=json.loads(receipt_bytes),
            bundle_receipt_sha256=sha256_bytes(receipt_bytes),
            bundle_receipt_bytes=receipt_bytes,
            bundle_receipt_relative_path="bundle.receipt.json",
            target_key=target,
            source_result_mappings=validated_map.source_result_mappings,
            read_bytes=lambda relative_path: output_dir.joinpath(
                *relative_path.split("/")
            ).read_bytes(),
        )
        assert len(validated.primary_rows) == expected
        assert len(validated.raw_rows) == expected


def test_adapter_rejects_metric_drift_from_v2_source(tmp_path: Path) -> None:
    fixture = _fixture_files(tmp_path)
    fixture["selection_rows"][0]["amp_read_log10_mic_um"] = "9.99"
    fixture["selection_path"].write_bytes(
        _csv_bytes(fixture["selection_rows"], fixture["selection_fields"])
    )

    with pytest.raises(ValueError, match="differs from v2 strict source columns"):
        _adapt(fixture, output_dir=tmp_path / "adapted")


def test_adapter_rejects_global_family_duplication(tmp_path: Path) -> None:
    fixture = _fixture_files(tmp_path)
    fixture["selection_rows"][1]["family_key_80_80"] = fixture["selection_rows"][0][
        "family_key_80_80"
    ]
    fixture["selection_path"].write_bytes(
        _csv_bytes(fixture["selection_rows"], fixture["selection_fields"])
    )

    with pytest.raises(ValueError, match="not globally family-unique"):
        _adapt(fixture, output_dir=tmp_path / "adapted")


def test_adapter_rejects_structure_overlap(tmp_path: Path) -> None:
    fixture = _fixture_files(tmp_path)
    family = fixture["selection_rows"][0]["family_key_80_80"]

    with pytest.raises(ValueError, match="overlaps structure history"):
        _adapt(
            fixture,
            output_dir=tmp_path / "adapted",
            exclusions=_exclusions(families=[family]),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("guruprasad_instability_index", "50", "fails the instability gate"),
        ("llamp_log10_mic_um", "", "lacks formal metric"),
    ],
)
def test_adapter_rejects_nonqualified_or_incomplete_formal_rows(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    fixture = _fixture_files(tmp_path)
    fixture["selection_rows"][0][field] = value
    fixture["selection_path"].write_bytes(
        _csv_bytes(fixture["selection_rows"], fixture["selection_fields"])
    )

    with pytest.raises(ValueError, match=message):
        _adapt(fixture, output_dir=tmp_path / "adapted")


def test_adapter_rejects_target_count_drift(tmp_path: Path) -> None:
    fixture = _fixture_files(tmp_path)

    with pytest.raises(ValueError, match="selection target counts differ"):
        _adapt(
            fixture,
            output_dir=tmp_path / "adapted",
            expected_counts={"acea": 2, "angpt1": 2},
        )
