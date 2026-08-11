from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROADMAP = ROOT / "docs" / "ampgent-long-horizon-goals.zh-CN.md"

PHASE_CONTRACTS = {
    "Q1": (
        "config/benchmarks/amp_charge_search_sufficiency_v33.yaml",
        "preregistered_draft_not_authorized",
    ),
    "Q2": (
        "config/benchmarks/amp_charge_search_sufficiency_v33.yaml",
        "preregistered_draft_not_authorized",
    ),
    "Q3": (
        "config/benchmarks/amp_knowledge_pepshot_ablation_v34.yaml",
        "preregistered_draft_not_authorized",
    ),
    "Q4": (
        "config/benchmarks/amp_knowledge_pepshot_ablation_v34.yaml",
        "preregistered_draft_not_authorized",
    ),
    "Q5": (
        "config/benchmarks/amp_multitarget_qualification_v35.yaml",
        "typed_persistence_implemented_not_deployed_not_authorized",
    ),
    "Q6": (
        "config/benchmarks/amp_harness_evolution_v36.yaml",
        "typed_schema_and_offline_verifier_implemented_not_deployed_not_authorized",
    ),
}

SYNTHETIC_GATES = (
    "config/benchmarks/amp_target_qualification_synthetic_acceptance_v35a.yaml",
    "config/benchmarks/amp_harness_synthetic_acceptance_v36a.yaml",
)


def test_long_horizon_roadmap_keeps_all_problem_contracts_and_statuses_aligned() -> None:
    roadmap = ROADMAP.read_text(encoding="utf-8")

    for question, (relative_path, expected_status) in PHASE_CONTRACTS.items():
        contract_path = ROOT / relative_path
        contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))

        assert f"### {question}：" in roadmap
        assert contract_path.name in roadmap
        assert contract["execution_status"] == expected_status
        assert expected_status in roadmap

    for relative_path in SYNTHETIC_GATES:
        contract_path = ROOT / relative_path
        contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        assert contract_path.name in roadmap
        assert contract["execution_status"] == "preregistered_not_authorized"
        assert contract["authorization"]["execution_authorized"] is False


def test_long_horizon_roadmap_preserves_capability_and_provider_boundaries() -> None:
    roadmap = ROADMAP.read_text(encoding="utf-8")

    for level in range(6):
        assert f"L{level}" in roadmap
    assert "项目整体处于 **L2" in roadmap
    assert "019fb910-f2dd-7be1-a7e6-bfe381512c25" in roadmap
    assert "不得由 AMPgent 自行适配" in roadmap
    assert "database-only replay" in roadmap
    assert "answered_within_scope" in roadmap
