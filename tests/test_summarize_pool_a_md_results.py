import json

import pytest

from analysis.summarize_pool_a_md_results import (
    MD_RELEASE,
    MMGBSA_RELEASE,
    summarize,
    validated_ingest_receipt,
)


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_summary_requires_exact_identity_and_reports_all_requested_outputs(tmp_path):
    candidate_id = "11111111-1111-1111-1111-111111111111"
    run_id = "22222222-2222-2222-2222-222222222222"
    sequence_sha = "a" * 64
    item = {
        "target_key": "acea",
        "candidate_id": candidate_id,
        "run_id": run_id,
        "sequence_sha256": sequence_sha,
        "pool_a_rank": 1,
        "primary_dg": -40.0,
    }
    snapshot = tmp_path / "snapshot.json"
    write(snapshot, {"pool_a_all": [item]})
    candidate = tmp_path / "evidence/acea" / candidate_id
    write(candidate / "launch_receipt.json", item)
    write(
        candidate / "manifest.json",
        {"status": "succeeded", "npt_ns": 1.0, "production_ns": 50.0},
    )
    write(
        candidate / "analysis/interface/interface_analysis.json",
        {
            "schema_version": "ampgent.pool-a-md-interface-analysis.2",
            "interface_rmsd_nm": {"mean": 0.2, "maximum": 0.5},
            "native_contact_fraction": {"mean": 0.8, "minimum": 0.4},
            "key_contacts": [{"occupancy": 0.75}],
            "hydrogen_bond_occupancy": 0.6,
            "salt_bridge_occupancy": 0.4,
            "water_bridge_occupancy": 0.5,
            "peptide_departed": False,
            "maximum_departure_duration_ps": 0,
            "maximum_peptide_com_shift_nm": 0.3,
        },
    )
    write(
        candidate / "analysis/mmgbsa/mmgbsa_analysis.json",
        {
            "schema_version": "ampgent.pool-a-mmgbsa.1",
            "mean_binding_energy_kcal_mol": -35,
            "confidence_interval_95_kcal_mol": [-38, -32],
            "frame_count": 36,
            "decomposition_residue_count": 1,
        },
    )
    (candidate / "analysis/mmgbsa/residue_decomposition_mean.csv").write_text(
        "residue,mean_TOTAL\nALA1,-1\n"
    )
    ingest_identity = {
        "candidate_id": candidate_id,
        "subject_run_id": run_id,
        "tool_call_id": "33333333-3333-3333-3333-333333333333",
    }
    write(
        candidate / "analysis/interface/postgresql_ingest_receipt.json",
        {**ingest_identity, "model_release_key": MD_RELEASE},
    )
    write(
        candidate / "analysis/mmgbsa/postgresql_ingest_receipt.json",
        {**ingest_identity, "model_release_key": MMGBSA_RELEASE},
    )
    write(
        candidate / "failure_receipt.json",
        {"returncode": 1, "will_retry": True},
    )
    result = summarize(snapshot, tmp_path / "evidence", tmp_path / "report")
    assert result["overall"]["expected_candidate_count"] == 1
    assert result["overall"]["pool_s_evidence_complete_count"] == 1
    assert result["overall"]["mmgbsa_mean_kcal_mol"] == -35
    assert result["overall"]["pending_md_count"] == 0
    assert result["overall"]["retry_failure_recorded_count"] == 1
    assert result["overall"]["postgresql_evidence_complete_count"] == 1
    rmsd = result["overall"]["metric_distributions"]["interface_rmsd_mean_nm"]
    assert rmsd == {
        "observed_count": 1,
        "missing_count": 0,
        "unit": "nm",
        "favorable_direction": "lower",
        "minimum": 0.2,
        "maximum": 0.2,
        "mean": 0.2,
        "median": 0.2,
        "population_standard_deviation": 0.0,
        "p10": 0.2,
        "p25": 0.2,
        "p75": 0.2,
        "p90": 0.2,
        "best_candidate": {
            "candidate_id": candidate_id,
            "run_id": run_id,
            "sequence_sha256": sequence_sha,
            "value": 0.2,
        },
        "worst_candidate": {
            "candidate_id": candidate_id,
            "run_id": run_id,
            "sequence_sha256": sequence_sha,
            "value": 0.2,
        },
    }
    assert result["overall"]["peptide_departure_categories"] == {
        "observed_count": 1,
        "missing_count": 0,
        "departed_count": 0,
        "retained_count": 1,
    }
    assert result["completion_receipt"] == str(
        (tmp_path / "report/completion_receipt.json").resolve()
    )
    completion = json.loads((tmp_path / "report/completion_receipt.json").read_text())
    assert completion["status"] == "succeeded"
    assert completion["expected_candidate_count"] == 1
    assert not (tmp_path / "report/.candidates.csv.tmp").exists()
    assert not (tmp_path / "report/.summary.json.tmp").exists()
    assert not (tmp_path / "report/.completion_receipt.json.tmp").exists()
    report = (tmp_path / "report/candidates.csv").read_text()
    for field in (
        "interface_rmsd_mean_nm",
        "key_contact_occupancy_mean",
        "hydrogen_bond_occupancy",
        "salt_bridge_occupancy",
        "water_bridge_occupancy",
        "peptide_departed",
        "mmgbsa_ci95_lower_kcal_mol",
        "decomposition_residue_count",
    ):
        assert field in report


def test_postgresql_receipt_identity_drift_is_rejected(tmp_path):
    receipt = tmp_path / "postgresql_ingest_receipt.json"
    write(
        receipt,
        {
            "candidate_id": "11111111-1111-1111-1111-111111111111",
            "subject_run_id": "wrong-run",
            "model_release_key": MD_RELEASE,
            "tool_call_id": "33333333-3333-3333-3333-333333333333",
        },
    )
    with pytest.raises(ValueError, match="subject_run_id"):
        validated_ingest_receipt(
            receipt,
            {
                "candidate_id": "11111111-1111-1111-1111-111111111111",
                "run_id": "22222222-2222-2222-2222-222222222222",
            },
            MD_RELEASE,
        )


def test_summary_rejects_decomposition_row_count_drift(tmp_path):
    from analysis.summarize_pool_a_md_results import validated_mmgbsa_evidence

    decomposition = tmp_path / "decomposition.csv"
    decomposition.write_text("residue,mean_TOTAL\nALA1,-1\n")
    with pytest.raises(ValueError, match="row count mismatch"):
        validated_mmgbsa_evidence(
            {
                "schema_version": "ampgent.pool-a-mmgbsa.1",
                "mean_binding_energy_kcal_mol": -10,
                "confidence_interval_95_kcal_mol": [-12, -8],
                "frame_count": 5,
                "decomposition_residue_count": 2,
            },
            decomposition,
        )
