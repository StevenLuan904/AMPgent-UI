import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
SNAPSHOT = ROOT / (
    "reports/pool_a_md_50ns_expansion_20260903/"
    "md_current_snapshot_20260904T074205Z.json"
)


def test_current_md_partition_and_pending_identity() -> None:
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    partition = payload["partition"]
    assert payload["identity_union"] == 486
    assert partition["launched_unique"] == 61
    assert partition["md_complete_unique"] == 45
    assert partition["full_evidence_unique"] == 44
    assert partition["analysis_pending_unique"] == 1
    assert partition["running_unique"] == 16
    assert partition["not_started_unique"] == 425
    assert partition["failed_unique"] == 0
    assert partition["launched_equals_md_complete_plus_running"]
    assert partition["full_plus_analysis_pending_equals_md_complete"]
    assert partition["disjoint_union_equals_486"]
    assert payload["analysis_pending"][0]["candidate_id"] == (
        "428925fe-b10f-435c-914d-a0f7b4a5f147"
    )


def test_pg_audit_matches_current_full_evidence() -> None:
    audit = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["postgresql_exact_audit"]
    assert audit["interface_candidate_count"] == 44
    assert audit["mmgbsa_candidate_count"] == 45
    assert audit["both_candidate_count"] == 44
    assert audit["interface_only_count"] == 0
    assert audit["mmgbsa_only_count"] == 1
    assert audit["local_full_missing_pg_both"] == 0
    assert audit["pg_both_missing_local_full"] == 0
