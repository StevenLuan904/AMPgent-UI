from analysis.verify_pool_a_md_full_completion import verify


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
