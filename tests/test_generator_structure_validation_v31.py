from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from pepagent.generator_structure_validation import (
    GeneratorStructureScreenManifest,
    freeze_balanced_structure_cohort,
    load_frozen_structure_cohort,
    write_frozen_outputs,
)


def _sequence(index: int) -> str:
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    return "K" + "".join(alphabet[(index + offset) % len(alphabet)] for offset in range(9))


def _write_source(path: Path, generators: dict[str, list[int]]) -> tuple[str, int]:
    rows = []
    serial = 0
    for generator_id, seeds in generators.items():
        for seed in seeds:
            for rank in range(1, 6):
                sequence = _sequence(serial)
                serial += 1
                rows.append(
                    {
                        "candidate_id": f"{generator_id}-{seed}-{rank}",
                        "generator_id": generator_id,
                        "seed": seed,
                        "selected_rank": rank,
                        "sequence": sequence,
                        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
                        "soft_score": 1000 - rank,
                    }
                )
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest(), len(rows)


def _manifest(tmp_path: Path) -> dict:
    first_sha, first_count = _write_source(
        tmp_path / "v23.csv", {"hydramp": [1], "ampgan_v2": [2]}
    )
    second_sha, second_count = _write_source(
        tmp_path / "v25.csv", {"amp_designer": [3]}
    )
    return {
        "benchmark_id": "amp_generator_target_structure_v31",
        "version": "v31.0.0",
        "execution_status": "preregistered",
        "phase": "balanced_fast_screen",
        "spec_path": "spec.yaml",
        "target_accession": "P0A9G6",
        "sources": [
            {
                "source_id": "v23",
                "path": "v23.csv",
                "sha256": first_sha,
                "expected_row_count": first_count,
            },
            {
                "source_id": "v25",
                "path": "v25.csv",
                "sha256": second_sha,
                "expected_row_count": second_count,
            },
        ],
        "generators": [
            {
                "generator_id": "hydramp",
                "source_id": "v23",
                "seeds": [1],
                "expected_source_rows_per_seed": 5,
                "selected_per_seed": 2,
            },
            {
                "generator_id": "ampgan_v2",
                "source_id": "v23",
                "seeds": [2],
                "expected_source_rows_per_seed": 5,
                "selected_per_seed": 2,
            },
            {
                "generator_id": "amp_designer",
                "source_id": "v25",
                "seeds": [3],
                "expected_source_rows_per_seed": 5,
                "selected_per_seed": 2,
            },
        ],
        "selection": {
            "method": "seed_stratified_maximin_levenshtein",
            "first_item_tiebreak": "sequence_sha256_then_source_rank",
            "subsequent_tiebreak": "source_rank_then_sequence_sha256",
            "soft_metric_columns_ignored": True,
            "pepmlm_used": False,
            "global_sequence_uniqueness_required": True,
        },
        "descriptor_qualification": {
            "maximum_identical_residue_run": 3,
            "maximum_hydrophobic_run": 5,
            "minimum_net_charge_ph7_4": -2.0,
            "maximum_net_charge_ph7_4": 10.0,
        },
        "output_cohort_path": "cohort.csv",
        "output_audit_path": "audit.json",
        "structure_claim_scope": "same_protocol_relative_target_specific_computational_evidence",
        "no_binding_or_affinity_claim": True,
        "frozen_predecessors_unchanged": True,
        "execution_authorized": False,
        "formal_run_limit": 1,
    }


def test_v31_freezes_equal_target_blind_diverse_quotas(tmp_path: Path) -> None:
    manifest = GeneratorStructureScreenManifest.model_validate(_manifest(tmp_path))
    rows, audit = freeze_balanced_structure_cohort(manifest, tmp_path)
    assert len(rows) == 6
    assert len({row["sequence"] for row in rows}) == 6
    assert {row["generator_id"] for row in rows} == {
        "hydramp",
        "ampgan_v2",
        "amp_designer",
    }
    assert audit["soft_metric_columns_ignored"] is True
    assert audit["pepmlm_used"] is False


def test_v31_rejects_source_hash_drift(tmp_path: Path) -> None:
    payload = _manifest(tmp_path)
    payload["sources"][0]["sha256"] = "0" * 64
    manifest = GeneratorStructureScreenManifest.model_validate(payload)
    with pytest.raises(ValueError, match="source SHA mismatch"):
        freeze_balanced_structure_cohort(manifest, tmp_path)


def test_v31_rejects_pepmlm_selection(tmp_path: Path) -> None:
    payload = _manifest(tmp_path)
    payload["selection"]["pepmlm_used"] = True
    with pytest.raises(ValueError, match="PepMLM"):
        GeneratorStructureScreenManifest.model_validate(payload)


def test_v31_frozen_loader_requires_exact_hash_order_and_quota(tmp_path: Path) -> None:
    payload = _manifest(tmp_path)
    manifest = GeneratorStructureScreenManifest.model_validate(payload)
    rows, audit = freeze_balanced_structure_cohort(manifest, tmp_path)
    cohort_path = tmp_path / "cohort.csv"
    audit_path = tmp_path / "audit.json"
    cohort_sha, audit_sha = write_frozen_outputs(rows, audit, cohort_path, audit_path)
    payload["execution_status"] = "cohort_frozen"
    payload["completion"] = {
        "implementation_revision": "a" * 40,
        "implementation_archive_sha256": "b" * 64,
        "cohort_path": "cohort.csv",
        "cohort_sha256": cohort_sha,
        "audit_path": "audit.json",
        "audit_sha256": audit_sha,
        "selected_count": 6,
    }
    frozen = GeneratorStructureScreenManifest.model_validate(payload)
    loaded = load_frozen_structure_cohort(frozen, tmp_path)
    assert [int(row["screening_rank"]) for row in loaded] == list(range(1, 7))
