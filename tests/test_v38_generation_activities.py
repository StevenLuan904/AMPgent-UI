import json
from uuid import uuid4

import pytest

from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.v38_science_execution import (
    V38_METRIC_OBSERVATIONS,
    build_default_v38_sequence_contract,
    unchanged_parent_control_sha256,
)
from pepagent.v38_sequence_first_multitarget import (
    KnowledgeUseTrace,
    SequenceRefinementPlan,
    SequenceRefinementTask,
)
from pepagent.workers import v38_activities
from pepagent.workers.v38_activities import (
    build_v38_metric_evaluation_rows,
    build_v38_score_all_cohort_from_results,
    validate_v38_refinement_result,
)


def _sequence(index: int) -> str:
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    residues = []
    value = index
    for _ in range(11):
        residues.append(alphabet[value % len(alphabet)])
        value //= len(alphabet)
    return "K" + "".join(residues)


def _generated_cells() -> list[dict]:
    contract = build_default_v38_sequence_contract()
    return [
        {
            "result": {
                "generator_id": cell.generator_id,
                "seed": cell.seed,
                "raw_proposal_budget": 100,
                "records": [
                    {
                        "raw_rank": rank,
                        "sequence": _sequence(cell.ordinal * 100 + rank),
                    }
                    for rank in range(1, 101)
                ],
            }
        }
        for cell in contract.cells
    ]


def test_generation_results_form_one_complete_score_all_cohort() -> None:
    contract = build_default_v38_sequence_contract()
    cohort = build_v38_score_all_cohort_from_results(contract, _generated_cells())
    assert cohort.raw_occurrence_count == 900
    assert cohort.promoted_unique_count == 900
    assert cohort.invalid_count == 0
    assert cohort.duplicate_count == 0


def test_generation_results_reject_missing_or_duplicate_cells() -> None:
    contract = build_default_v38_sequence_contract()
    generated = _generated_cells()
    with pytest.raises(ValueError, match="exactly cover"):
        build_v38_score_all_cohort_from_results(contract, generated[:-1])
    with pytest.raises(ValueError, match="duplicated"):
        build_v38_score_all_cohort_from_results(
            contract,
            [generated[0], generated[0], *generated[2:]],
        )


def test_generation_results_reject_noncontiguous_raw_ranks() -> None:
    contract = build_default_v38_sequence_contract()
    generated = _generated_cells()
    generated[0]["result"]["records"][4]["raw_rank"] = 99
    with pytest.raises(ValueError, match="raw ranks"):
        build_v38_score_all_cohort_from_results(contract, generated)


def _metric_result(plugin_name: str, candidates: list[dict]) -> dict:
    records = []
    for candidate in candidates:
        observations = []
        for metric_name in sorted(V38_METRIC_OBSERVATIONS[plugin_name]):
            is_label = metric_name.endswith("_label")
            observations.append(
                {
                    "metric_name": metric_name,
                    "numeric_value": None if is_label else 0.25,
                    "text_value": "low" if is_label else None,
                    "unit": None,
                }
            )
        records.append(
            {
                "candidate_id": candidate["id"],
                "sequence": candidate["sequence"],
                "status": "complete",
                "observations": observations,
                "raw": {"source": "test"},
            }
        )
    return {
        "result": {
            "plugin": {"name": plugin_name},
            "contract": {
                "reliability": "frozen",
                "default_trust": "screening",
            },
            "status": "complete",
            "records": records,
        },
        "provenance": {},
    }


def test_all_five_metric_plugins_cover_every_candidate_and_exactly_12_metrics() -> None:
    contract = build_default_v38_sequence_contract()
    candidates = [
        {"id": "candidate-a", "sequence": "KACDEFGHIKLM"},
        {"id": "candidate-b", "sequence": "KLMNPQRSTVWY"},
    ]
    observed = set()
    total_rows = 0
    for plugin_name in contract.metric_plugins:
        rows = build_v38_metric_evaluation_rows(
            contract=contract,
            candidates=candidates,
            metric_result=_metric_result(plugin_name, candidates),
        )
        assert len(rows) == len(candidates) * len(V38_METRIC_OBSERVATIONS[plugin_name])
        observed.update(row["metric_name"] for row in rows)
        total_rows += len(rows)
    assert observed == set(contract.required_sequence_metrics)
    assert total_rows == 24


def test_metric_rows_fail_closed_when_one_candidate_observation_is_missing() -> None:
    contract = build_default_v38_sequence_contract()
    candidates = [{"id": "candidate-a", "sequence": "KACDEFGHIKLM"}]
    result = _metric_result("toxicity_risk", candidates)
    result["result"]["records"][0]["observations"].pop()
    with pytest.raises(ValueError, match="missing declared"):
        build_v38_metric_evaluation_rows(
            contract=contract,
            candidates=candidates,
            metric_result=result,
        )


@pytest.mark.asyncio
async def test_sequence_admission_reference_resolves_and_rejects_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "schema_version": "v38.sequence-admission-evidence.1",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "admission": {"mature_core_candidate_ids": []},
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    class Store:
        def get_bytes(self, _uri: str) -> bytes:
            return raw

    monkeypatch.setattr(v38_activities, "ContentAddressedObjectStore", Store)
    reference = {
        "schema_version": v38_activities.V38_ADMISSION_REFERENCE_SCHEMA,
        "admission_sha256": sha256_json(payload),
        "admission_artifact": {
            "sha256": sha256_bytes(raw),
            "size_bytes": len(raw),
            "uri": "s3://evidence/admission.json",
            "media_type": "application/json",
        },
    }
    assert await v38_activities._resolve_v38_admission(reference) == payload

    reference["admission_artifact"]["size_bytes"] += 1
    with pytest.raises(ValueError, match="identity"):
        await v38_activities._resolve_v38_admission(reference)


def test_refinement_result_exactly_covers_plan_and_retains_parent_control() -> None:
    parent_id = uuid4()
    parent_sequence = "KACDEFGHIKLM"
    task = SequenceRefinementTask(
        parent_candidate_id=parent_id,
        parent_sequence=parent_sequence,
        parent_sequence_sha256=sha256_bytes(parent_sequence.encode()),
        refinement_round=1,
        requested_children=2,
        knowledge_context_pack_sha256="a" * 64,
        objective_metric_names=(
            "llamp_log10_mic_um",
            "amp_read_log10_mic_um",
        ),
        parent_control_sha256=unchanged_parent_control_sha256(
            parent_candidate_id=parent_id,
            parent_sequence=parent_sequence,
            refinement_round=1,
        ),
    )
    plan = SequenceRefinementPlan(
        refinement_round=1,
        admission_sha256="b" * 64,
        tasks=(task,),
    )
    trace = KnowledgeUseTrace(
        card_id="general-amp-amphipathicity",
        query_sha256="c" * 64,
        passage_sha256="d" * 64,
        decision="adopt",
        rationale="test a conservative amphipathicity edit",
    )
    proposals = [
        {
            "parent_candidate_id": str(parent_id),
            "parent_sequence": parent_sequence,
            "child_sequence": child,
            "refinement_round": 1,
            "mutation_rationale": "single conservative residue edit",
            "knowledge_traces": [trace.model_dump(mode="json")],
            "unchanged_parent_control_sha256": task.parent_control_sha256,
        }
        for child in ("KACDEFGHIKLL", "KACDEFGHIKKM")
    ]
    observed = validate_v38_refinement_result(plan, {"proposals": proposals})
    assert len(observed) == 2
    assert {item.parent_candidate_id for item in observed} == {parent_id}

    with pytest.raises(ValueError, match="exactly cover"):
        validate_v38_refinement_result(plan, {"proposals": proposals[:1]})
