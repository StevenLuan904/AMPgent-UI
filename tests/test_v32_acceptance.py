from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import yaml

from pepagent.v32_acceptance import build_acceptance_exports

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_multiobjective_acceptance_v32.yaml"


def _candidate(index: int) -> dict:
    seed = 20261101 + index % 3
    sequence = "ACDEFGHIKL" + "MNPQRSTVWY"[index % 10]
    return {
        "id": f"candidate-{index}",
        "sequence": sequence,
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "generation": 0,
        "proposal_rank": index + 1,
        "status": "selected" if index < 24 else "rejected",
        "generator_call_id": f"call-{seed}",
        "metadata": {"generator_seed": seed, "raw_rank": index + 1},
    }


def _portfolio_item(index: int) -> dict:
    lane = ("membrane", "activity_mic", "risk_control", "balanced")[index // 6]
    seed = 20261101 + index % 3
    candidate = _candidate(index)
    metrics = {
        "net_charge_ph7_4": float(index + 1),
        "hydrophobic_moment_eisenberg": 0.4 + index / 100,
        "hydrophobic_ratio_modlamp": 0.5,
        "maximum_hydrophobic_run": 2.0,
        "macrel_amp_probability": 0.6,
        "llamp_log10_mic_um": 1.0,
        "llamp_predicted_mic_um": 10.0,
        "amp_read_log10_mic_um": 1.1,
        "amp_read_predicted_mic_um": 12.0,
        "toxinpred3_hybrid_score": 0.2,
        "macrel_hemolysis_probability": 0.3,
    }
    return {
        "lane": lane,
        "lane_rank": index % 6 + 1,
        "candidate_id": candidate["id"],
        "seed": seed,
        "sequence": candidate["sequence"],
        "sequence_sha256": candidate["sequence_sha256"],
        "family_depths": {"membrane": 1, "activity_mic": 1, "risk_control": 1},
        "metrics": metrics,
        "labels": {"toxinpred3_label": "Non-Toxin", "macrel_hemolysis_label": "low"},
        "claim_scope": "computational_multiobjective_hypothesis_only",
    }


def test_acceptance_contract_is_read_only_and_frozen_for_one_run() -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert payload["execution_authorized"] is True
    assert payload["execution_status"] == "ready"
    assert payload["implementation"]["revision"] == (
        "9b70351250c30687c459a1297a7ff8ffa5b2291f"
    )
    assert payload["formal_acceptance_run"]["submitted"] is False
    assert payload["scientific_contract"]["parent_run_read_only"] is True
    assert payload["scientific_contract"]["no_new_generation"] is True
    assert payload["scientific_contract"]["no_parent_backwrite"] is True


def test_acceptance_exports_are_deterministic_and_v33_ready() -> None:
    contract = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    candidates = [_candidate(index) for index in range(300)]
    selected = [_portfolio_item(index) for index in range(24)]
    evaluations = []
    for item in selected:
        values = {**item["metrics"], **item["labels"]}
        for name, value in values.items():
            evaluations.append(
                {
                    "candidate_id": item["candidate_id"],
                    "metric_name": name,
                    "numeric_value": value if isinstance(value, float) else None,
                    "text_value": value if isinstance(value, str) else None,
                }
            )
    template = {**selected[0]["metrics"], **selected[0]["labels"]}
    for candidate in candidates[24:]:
        for name, value in template.items():
            evaluations.append(
                {
                    "candidate_id": candidate["id"],
                    "metric_name": name,
                    "numeric_value": value if isinstance(value, float) else None,
                    "text_value": value if isinstance(value, str) else None,
                }
            )
    graph = {
        "graph_sha256": "a" * 64,
        "candidates": candidates,
        "tool_calls": [{}] * 10,
        "evaluations": evaluations,
        "tool_call_dependencies": [{}] * 24,
        "agent_decisions": [{}],
    }
    portfolio = {
        "lane_results": selected,
        "excluded_risk_red_candidate_ids": [],
        "eligible_count": 300,
        "concordant_risk_red_count": 0,
        "selected_count": 24,
        "weighted_total_score_used": False,
        "charge_optimized": False,
    }
    first = build_acceptance_exports(graph, portfolio, contract)
    second = build_acceptance_exports(graph, portfolio, contract)
    assert first == second
    all_rows = list(csv.DictReader(io.StringIO(first["all_candidates_csv"].decode())))
    selected_rows = list(
        csv.DictReader(io.StringIO(first["portfolio_candidates_csv"].decode()))
    )
    lane_rows = list(csv.DictReader(io.StringIO(first["lane_summary_csv"].decode())))
    manifest = json.loads(first["acceptance_manifest_json"])
    assert len(all_rows) == 300
    assert len(selected_rows) == 24
    assert len(lane_rows) == 4
    assert manifest["verdict"] == "ready_for_v33_preregistration"
    assert all(manifest["v33_readiness_gates"].values())
