import hashlib
import json

from analysis.ingest_pool_a_md_evidence import MD_RELEASE, MD_RELEASE_V2, MMGBSA_RELEASE
from analysis.verify_pool_a_md_full_completion import audit_candidate, verify


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def receipt_files(root, paths):
    return {
        key: {"uri": f"/remote/results/acea/candidate-1/{path}", "sha256": hashlib.sha256(
            (root / path).read_bytes()
        ).hexdigest()}
        for key, path in paths.items()
    }


def evidence_root(tmp_path, *, schema="ampgent.pool-a-md-interface-analysis.2", network=True):
    root = tmp_path / "evidence"
    candidate = root / "acea" / "candidate-1"
    write_json(
        candidate / "launch_receipt.json",
        {
            "target_key": "acea",
            "run_id": "run-1",
            "candidate_id": "candidate-1",
            "sequence_sha256": "a" * 64,
        },
    )
    write_json(
        candidate / "manifest.json",
        {
            "schema_version": "ampgent.pool-a-md.1",
            "status": "succeeded",
            "npt_ns": 1,
            "production_ns": 50,
        },
    )
    interface = {
        "schema_version": schema,
        "interface_rmsd_nm": {"mean": 0.2, "maximum": 0.4},
        "native_contact_fraction": {"mean": 0.8, "minimum": 0.5},
        "key_contacts": [{"occupancy": 0.7}],
        "hydrogen_bond_occupancy": 0.6,
        "salt_bridge_occupancy": 0.4,
        "water_bridge_occupancy": 0.5,
        "peptide_departed": False,
        "maximum_departure_duration_ps": 0,
        "maximum_peptide_com_shift_nm": 0.3,
    }
    if network:
        interface["hydrogen_bond_network"] = {
            "bond_count_per_frame": {"mean": 1, "median": 1, "maximum": 2, "p95": 2},
            "unique_atom_pair_count": 1,
            "unique_residue_pair_count": 1,
            "persistent_residue_pair_count": 1,
            "residue_pairs": [],
            "atom_pairs": [],
        }
    write_json(candidate / "analysis/interface/interface_analysis.json", interface)
    (candidate / "analysis/interface/timeseries.csv").parent.mkdir(parents=True, exist_ok=True)
    (candidate / "analysis/interface/timeseries.csv").write_text("time_ps\n0\n", encoding="utf-8")
    if schema.endswith(".3") and network:
        (candidate / "analysis/interface/hydrogen_bond_timeseries.csv").write_text(
            "time_ps,direct_hydrogen_bond_count\n0,1\n", encoding="utf-8"
        )
    write_json(
        candidate / "analysis/mmgbsa/mmgbsa_analysis.json",
        {
            "schema_version": "ampgent.pool-a-mmgbsa.1",
            "mean_binding_energy_kcal_mol": -35,
            "confidence_interval_95_kcal_mol": [-38, -32],
            "decomposition_residue_count": 1,
        },
    )
    decomposition = candidate / "analysis/mmgbsa/residue_decomposition_mean.csv"
    decomposition.parent.mkdir(parents=True, exist_ok=True)
    decomposition.write_text(
        "residue,mean_Internal,mean_van der Waals,mean_Electrostatic,"
        "mean_Polar Solvation,mean_Non-Polar Solv.,mean_TOTAL\n"
        "ALA 1,0,-1,-1,1,-1,-2\n",
        encoding="utf-8",
    )
    interface_paths = {
        "manifest": "manifest.json",
        "interface_analysis": "analysis/interface/interface_analysis.json",
        "timeseries": "analysis/interface/timeseries.csv",
    }
    if schema.endswith(".3") and network:
        interface_paths["hydrogen_bond_timeseries"] = (
            "analysis/interface/hydrogen_bond_timeseries.csv"
        )
    write_json(
        candidate / "analysis/interface/postgresql_ingest_receipt.json",
        {
            "candidate_id": "candidate-1",
            "subject_run_id": "run-1",
            "model_release_key": MD_RELEASE if schema.endswith(".3") else MD_RELEASE_V2,
            "tool_call_id": "33333333-3333-3333-3333-333333333333",
            "inserted_evaluation_count": 11,
            "files": receipt_files(candidate, interface_paths),
        },
    )
    write_json(
        candidate / "analysis/mmgbsa/postgresql_ingest_receipt.json",
        {
            "candidate_id": "candidate-1",
            "subject_run_id": "run-1",
            "model_release_key": MMGBSA_RELEASE,
            "tool_call_id": "44444444-4444-4444-4444-444444444444",
            "inserted_evaluation_count": 4,
            "files": receipt_files(
                candidate,
                {
                    "mmgbsa_analysis": "analysis/mmgbsa/mmgbsa_analysis.json",
                    "residue_decomposition": "analysis/mmgbsa/residue_decomposition_mean.csv",
                },
            ),
        },
    )
    return root


def audit_row():
    return {
        "target_key": "acea",
        "run_id": "run-1",
        "candidate_id": "candidate-1",
        "sequence": "A",
        "sequence_sha256": "a" * 64,
    }


def test_candidate_audit_closes_from_small_evidence_without_trajectory(tmp_path):
    result = audit_candidate(audit_row(), evidence_root(tmp_path))
    assert result["full_evidence_complete"] is True
    assert result["gap_reasons"] == []


def test_candidate_audit_fails_closed_for_v3_missing_hbond_network(tmp_path):
    result = audit_candidate(
        audit_row(),
        evidence_root(
            tmp_path,
            schema="ampgent.pool-a-md-interface-analysis.3",
            network=False,
        ),
    )
    assert result["full_evidence_complete"] is False
    assert {
        item["reason"] for item in result["gap_reasons"]
    } >= {"interface_v3_hydrogen_bond_network_missing_or_incomplete"}


def test_candidate_audit_requires_exact_pg_receipt_binding(tmp_path):
    root = evidence_root(tmp_path)
    receipt = root / "acea/candidate-1/analysis/mmgbsa/postgresql_ingest_receipt.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["subject_run_id"] = "other-run"
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    result = audit_candidate(audit_row(), root)
    assert result["full_evidence_complete"] is False
    assert {
        item["reason"] for item in result["gap_reasons"]
    } >= {"postgresql_ingest_run_identity_drift"}


def test_candidate_audit_accepts_legacy_receipt_without_artifact_hashes(tmp_path):
    root = evidence_root(tmp_path)
    for relative in (
        "analysis/interface/postgresql_ingest_receipt.json",
        "analysis/mmgbsa/postgresql_ingest_receipt.json",
    ):
        path = root / "acea/candidate-1" / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("files")
        payload["inserted_evaluation_count"] = 0
        payload["already_complete"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")
    result = audit_candidate(audit_row(), root)
    assert result["full_evidence_complete"] is True
    assert result["gap_reasons"] == []
    assert result["checks"]["postgresql_interface"]["provenance_strength"] == (
        "receipt_only_legacy_no_artifact_hashes"
    )


def payloads(complete: bool = True) -> dict:
    candidate = {"run_id": "run-1", "candidate_id": "candidate-1"}
    count = 1 if complete else 0
    return {
        "summary": {
            "schema_version": "ampgent.pool-a-md-summary.1",
            "overall": {
                "expected_candidate_count": 1,
                "md_complete_count": count,
                "interface_complete_count": count,
                "mmgbsa_complete_count": count,
                "pool_s_evidence_complete_count": count,
                "postgresql_evidence_complete_count": count,
            },
        },
        "gap": {
            "schema_version": "ampgent.pool-a-md-gap-manifest.1",
            "candidate_count": 1,
            "issue_counts": {},
            "candidates": [
                {**candidate, "stage": "complete" if complete else "not_launched"}
            ],
        },
        "contacts": {
            "schema_version": "ampgent.pool-a-key-contact-occupancy.1",
            "pool_a_candidate_count": 1,
            "interface_and_postgresql_complete_count": count,
            "candidates": [dict(candidate)] if complete else [],
        },
        "residues": {
            "schema_version": "ampgent.pool-a-peptide-residue-decomposition.1",
            "pool_a_candidate_count": 1,
            "decomposition_complete_count": count,
            "candidates": [dict(candidate)] if complete else [],
        },
        "frontier": {
            "schema_version": "ampgent.pool-s-provisional-md-pareto.2",
            "pool_a_candidate_count": 1,
            "md_and_postgresql_complete_count": count,
            "weighted_total_used": False,
        },
        "dossiers": {
            "schema_version": "ampgent.pool-s-candidate-dossiers.1",
            "pool_a_candidate_count": 1,
            "complete_dossier_count": count,
            "dossiers": [dict(candidate)] if complete else [],
        },
    }


def test_complete_only_when_every_identity_and_evidence_closes():
    result = verify(**payloads())
    assert result["status"] == "complete"
    assert result["all_required_evidence_complete"] is True
    assert result["consistency_errors"] == []


def test_in_progress_is_valid_when_reports_agree_on_pending_candidate():
    result = verify(**payloads(complete=False))
    assert result["status"] == "in_progress"
    assert result["pending_candidate_count"] == 1
    assert result["consistency_errors"] == []


def test_identity_mismatch_prevents_completion():
    inputs = payloads()
    inputs["contacts"]["candidates"][0]["candidate_id"] = "wrong-candidate"
    result = verify(**inputs)
    assert result["status"] == "in_progress"
    assert result["consistency_error_count"] == 1
    assert result["cross_report_identity_mismatches"]["contacts"] == {
        "missing_complete_identity_count": 1,
        "unexpected_identity_count": 1,
        "valid_partial_identity_count": 0,
    }


def test_in_progress_accepts_valid_partial_mmgbsa_evidence():
    inputs = payloads()
    partial = {"run_id": "run-2", "candidate_id": "candidate-2"}
    inputs["summary"]["overall"] |= {
        "expected_candidate_count": 2,
        "md_complete_count": 2,
        "interface_complete_count": 1,
        "mmgbsa_complete_count": 2,
    }
    inputs["gap"]["candidate_count"] = 2
    inputs["gap"]["candidates"].append({**partial, "stage": "mmgbsa_complete"})
    inputs["contacts"]["pool_a_candidate_count"] = 2
    inputs["residues"]["pool_a_candidate_count"] = 2
    inputs["residues"]["decomposition_complete_count"] = 2
    inputs["residues"]["candidates"].append(partial)
    inputs["frontier"]["pool_a_candidate_count"] = 2
    inputs["dossiers"]["pool_a_candidate_count"] = 2
    result = verify(**inputs)
    assert result["status"] == "in_progress"
    assert result["complete_candidate_count"] == 1
    assert result["pending_candidate_count"] == 1
    assert result["consistency_errors"] == []
    assert result["cross_report_identity_mismatches"]["residues"] == {
        "missing_complete_identity_count": 0,
        "unexpected_identity_count": 0,
        "valid_partial_identity_count": 1,
    }
