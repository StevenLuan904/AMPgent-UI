from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from pepagent.v35_acceptance import V35AAcceptanceContract, load_v35a_acceptance_contract

ROOT = Path(__file__).parents[1]
CONFIG = (
    ROOT
    / "config"
    / "benchmarks"
    / "amp_target_qualification_synthetic_acceptance_v35a.yaml"
)


def _payload() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_v35a_contract_is_strict_hash_pinned_and_not_authorized() -> None:
    contract = load_v35a_acceptance_contract(CONFIG)
    assert contract.execution_status == "preregistered_not_authorized"
    assert (
        contract.implementation_revision
        == "41aba8ba08405cde65479bfd802fd2c6b2891598"
    )
    assert contract.authorization.execution_authorized is False
    assert contract.authorization.submitted is False
    assert contract.authorization.run_id is None
    assert (
        contract.authorization.explicit_user_phrase_required
        == "授权 v35a 靶点资格合成数据库闭环验收"
    )


def test_v35a_has_zero_candidate_boundary_and_exact_typed_counts() -> None:
    contract = load_v35a_acceptance_contract(CONFIG)
    assert contract.data_boundary.synthetic_only
    assert contract.data_boundary.real_target_names_or_accessions_forbidden
    assert contract.data_boundary.candidate_count == 0
    assert contract.data_boundary.evaluation_count == 0
    assert contract.expected_typed_counts.target_qualification_audit_count == 8
    assert contract.expected_typed_counts.target_panel_selection_member_count == 3
    assert contract.expected_typed_counts.candidate_count == 0
    assert contract.expected_typed_counts.evaluation_count == 0


def test_v35a_freezes_denominator_negative_probes_and_database_replay() -> None:
    contract = load_v35a_acceptance_contract(CONFIG)
    scenario = contract.synthetic_scenario
    assert scenario.shortlist_count == scenario.qualified_primary_count + scenario.rejected_count
    assert scenario.selection_depends_on_all_audit_calls
    assert scenario.peptide_or_structure_outcomes_used_for_selection is False
    assert len(contract.negative_probes) == 7
    evidence = contract.database_evidence_contract
    assert evidence.PostgreSQL_is_authoritative
    assert evidence.persist_all_success_and_failed_probe_ToolCalls
    assert evidence.database_object_store_only_replay_required
    assert evidence.replay_must_reconstruct_complete_failure_denominator
    assert evidence.replay_must_reconstruct_exact_selected_order


def test_v35a_rejects_contract_drift_or_extra_fields() -> None:
    payload = _payload()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        V35AAcceptanceContract.model_validate(payload)

    payload = _payload()
    payload["negative_probes"] = copy.deepcopy(payload["negative_probes"][:-1])
    with pytest.raises(ValueError, match="negative-probe set drifted"):
        V35AAcceptanceContract.model_validate(payload)

    payload = _payload()
    payload["expected_typed_counts"]["tool_call_count"] = 15
    with pytest.raises(ValueError):
        V35AAcceptanceContract.model_validate(payload)

    payload = _payload()
    payload["expected_typed_counts"]["tool_call_dependency_count"] = 7
    with pytest.raises(ValueError):
        V35AAcceptanceContract.model_validate(payload)
