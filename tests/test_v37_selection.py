from pepagent.provenance.hashing import sha256_text
from pepagent.v37_selection import select_v37_lanes

OBJECTIVES = {
    "activity": [{"metric_name": "activity", "direction": "maximize"}],
    "risk": [{"metric_name": "risk", "direction": "minimize"}],
}


def _candidate(
    candidate_id: str,
    sequence: str,
    activity: float,
    risk: float,
    *,
    generator: str = "g1",
    seed: int = 1,
    labels: dict[str, str] | None = None,
    source_ordinal: int = 0,
) -> dict[str, object]:
    return {
        "id": candidate_id,
        "sequence": sequence,
        "sequence_sha256": sha256_text(sequence),
        "generator_id": generator,
        "seed": seed,
        "source_ordinal": source_ordinal,
        "metrics": {"activity": activity, "risk": risk},
        "labels": labels or {},
    }


def test_v37_selection_reports_shortfall_without_refill_or_scalarization() -> None:
    result = select_v37_lanes(
        [_candidate("a", "KRWAL", 0.9, 0.2)],
        lanes=[{"name": "activity", "quota": 2, "objective_families": ["activity"]}],
        family_objectives=OBJECTIVES,
        maximum_similarity=0.8,
        maximum_per_generator=2,
        maximum_per_generator_seed=2,
    )
    assert result["selected_ids"] == ["a"]
    assert result["witnesses"]["activity"]["shortfall"] == 1
    assert result["selection_complete"] is False
    assert result["weighted_total_used"] is False


def test_v37_selection_enforces_soft_labels_and_generator_caps() -> None:
    candidates = [
        _candidate(
            "a",
            "KRWAL",
            0.9,
            0.1,
            labels={"tox": "Non-Toxin"},
        ),
        _candidate(
            "b",
            "GILDK",
            0.8,
            0.2,
            labels={"tox": "Non-Toxin"},
        ),
        _candidate(
            "c",
            "MPRVT",
            1.0,
            0.0,
            generator="g2",
            labels={"tox": "Toxin"},
        ),
    ]
    result = select_v37_lanes(
        candidates,
        lanes=[
            {
                "name": "balanced",
                "quota": 2,
                "objective_families": ["activity", "risk"],
                "required_soft_labels": {"tox": "Non-Toxin"},
            }
        ],
        family_objectives=OBJECTIVES,
        maximum_similarity=0.8,
        maximum_per_generator=1,
        maximum_per_generator_seed=1,
    )
    assert len(result["selected_ids"]) == 1
    assert "c" not in result["selected_ids"]
    assert result["witnesses"]["balanced"]["eligible_count"] == 2


def test_v37_selection_globally_excludes_only_concordant_red_flags() -> None:
    candidates = [
        _candidate(
            "both-red",
            "KRWAL",
            1.0,
            0.0,
            labels={
                "toxinpred3_label": "Toxin",
                "macrel_hemolysis_label": "high",
            },
            source_ordinal=1,
        ),
        _candidate(
            "tox-only",
            "GILDK",
            0.9,
            0.1,
            labels={
                "toxinpred3_label": "Toxin",
                "macrel_hemolysis_label": "low",
            },
            source_ordinal=2,
        ),
        _candidate(
            "hemo-only",
            "MPRVT",
            0.8,
            0.2,
            labels={
                "toxinpred3_label": "Non-Toxin",
                "macrel_hemolysis_label": "high",
            },
            source_ordinal=3,
        ),
    ]
    result = select_v37_lanes(
        candidates,
        lanes=[{"name": "activity", "quota": 3, "objective_families": ["activity"]}],
        family_objectives=OBJECTIVES,
        maximum_similarity=1.0,
        maximum_per_generator=3,
        maximum_per_generator_seed=3,
    )

    assert result["excluded_ids"] == ["both-red"]
    assert result["selected_ids"] == ["tox-only", "hemo-only"]
    witness = result["risk_guard_witness"]
    assert witness["excluded_count"] == 1
    assert witness["single_model_warning_remains_eligible"] is True
    assert witness["no_refill"] is True
    assert witness["excluded"][0]["toxicity_label"] == "Toxin"
    assert witness["excluded"][0]["hemolysis_label"] == "high"


def test_v37_selection_order_is_independent_of_candidate_uuid() -> None:
    base = [
        _candidate("uuid-a", "KRWAL", 0.9, 0.1, source_ordinal=2),
        _candidate("uuid-b", "GILDK", 0.9, 0.1, source_ordinal=1),
    ]
    perturbed = [{**item, "id": f"changed-{item['id']}"} for item in base]
    arguments = {
        "lanes": [{"name": "activity", "quota": 2, "objective_families": ["activity"]}],
        "family_objectives": OBJECTIVES,
        "maximum_similarity": 1.0,
        "maximum_per_generator": 2,
        "maximum_per_generator_seed": 2,
    }

    first = select_v37_lanes(base, **arguments)
    second = select_v37_lanes(perturbed, **arguments)
    first_by_id = {item["id"]: item["sequence"] for item in base}
    second_by_id = {item["id"]: item["sequence"] for item in perturbed}
    assert [first_by_id[item] for item in first["selected_ids"]] == [
        second_by_id[item] for item in second["selected_ids"]
    ]
