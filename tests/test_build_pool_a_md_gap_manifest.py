import copy

import pytest

from analysis.build_pool_a_md_gap_manifest import build


def row(candidate_id="candidate-1"):
    return {
        "target_key": "acea",
        "run_id": "run-1",
        "candidate_id": candidate_id,
        "sequence": "AK",
        "sequence_sha256": "a" * 64,
        "pool_a_rank": "1",
        "md_launched": "True",
        "md_complete": "True",
        "interface_complete": "True",
        "mmgbsa_complete": "True",
        "interface_postgresql_ingested": "True",
        "mmgbsa_postgresql_ingested": "True",
        "postgresql_evidence_complete": "True",
        "pool_s_evidence_complete": "True",
    }


def test_classifies_complete_and_unlaunched_candidates():
    complete = row()
    pending = copy.deepcopy(row("candidate-2"))
    pending["pool_a_rank"] = "2"
    for field in (
        "md_launched",
        "md_complete",
        "interface_complete",
        "mmgbsa_complete",
        "interface_postgresql_ingested",
        "mmgbsa_postgresql_ingested",
        "postgresql_evidence_complete",
        "pool_s_evidence_complete",
    ):
        pending[field] = "False"
    payload = build([complete, pending])
    assert payload["stage_counts"] == {"complete": 1, "not_launched": 1}
    assert payload["issue_counts"] == {}
    assert payload["noncanonical_smoke_outputs_counted"] is False


def test_reports_analysis_without_canonical_md_completion():
    value = row()
    value["md_complete"] = "False"
    value["pool_s_evidence_complete"] = "False"
    value["postgresql_evidence_complete"] = "False"
    payload = build([value])
    assert payload["stage_counts"] == {"md_running_or_checkpoint_pending": 1}
    assert payload["issue_counts"] == {"analysis_complete_without_md_complete": 1}


def test_rejects_duplicate_exact_identity():
    value = row()
    with pytest.raises(ValueError, match="duplicate Pool-A MD identity"):
        build([value, value])
