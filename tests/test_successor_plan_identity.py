import pytest

from analysis.successor_plan_identity import (
    derive_operation_identity,
    frozen_source_paths,
    source_root_from_freeze,
)

ROOT = "f72805f4-7547-5017-a069-74042708d228"
RUN = "61d65749-dab0-55db-b62a-6834e4fe604d"


def test_identity_separates_round_version_and_input():
    args = {"root_id": ROOT, "run_id": RUN, "operation": "score-import", "version": "v2"}
    a = derive_operation_identity(**args, round_name="r139", input_identity="a")
    b = derive_operation_identity(**args, round_name="r140", input_identity="a")
    c = derive_operation_identity(**args, round_name="r139", input_identity="b")
    assert a != b and a != c
    assert a == derive_operation_identity(**args, round_name="r139", input_identity="a")


def test_source_paths_use_freeze_root_and_real_b_names():
    freeze = {"remote_source_root": "/data1/huangyueshan/pepagent/runs/campaign/"
              "postprocess_round140_qd_20261009/r140_evidence_sources_final"}
    paths = frozen_source_paths(
        freeze, enriched="final_enriched_authoritative.csv", formal="candidate_scores.csv",
        amplify="amplify_scores.csv", b_selection="r140_B_selection_official.json",
        b_manifest="r140_B_manifest_successor.json",
    )
    assert paths["B_selection"].endswith("/r140_B_selection_official.json")
    assert "/runs/campaign/postprocess_round140_qd_20261009/" in paths["enriched"]


@pytest.mark.parametrize("root", ["/data1/foo/postprocess_round140_qd_20261009", "/tmp/not-a-run"])
def test_source_root_rejects_hand_or_flat_paths(root):
    with pytest.raises(ValueError):
        source_root_from_freeze({"remote_source_root": root})
