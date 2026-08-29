from __future__ import annotations

import copy

import pytest

from pepagent.autoresearch_structure_cohort import TARGET_KEYS
from pepagent.provenance.hashing import sha256_text
from pepagent.structure_v2_reservation import (
    SOURCE_SCHEMA,
    parse_strict_source,
    reservation_key,
    select_fresh_rows,
)


def _sequence(index: int) -> str:
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    value = index
    residues = []
    for _ in range(10):
        residues.append(alphabet[value % len(alphabet)])
        value //= len(alphabet)
    return "K" + "".join(residues) + "R"


def _source_document(*, per_target: int = 51) -> dict[str, object]:
    rows = []
    ordinal = 2
    for target_index, target_key in enumerate(TARGET_KEYS):
        for index in range(per_target):
            global_index = target_index * 10_000 + index
            sequence = _sequence(global_index)
            rows.append(
                {
                    "source_row_ordinal": ordinal,
                    "source_candidate_id": f"{target_key}-source-{index:03d}",
                    "sequence": sequence,
                    "sequence_sha256": sha256_text(sequence),
                    "family_key_80_80": f"family-{target_key}-{index:03d}",
                    "target_key": target_key,
                    "strict_display_eligible": True,
                    "valid_sequence": True,
                    "toxinpred3_label": "Non-Toxin",
                    "macrel_hemolysis_label": "low",
                    "guruprasad_instability_index": 10.0 + index / 100,
                    "guruprasad_instability_ood": False,
                    "activity_model_support_count": 2 + index % 2,
                    "source_result_sha256": sha256_text(f"source-result-{global_index}"),
                }
            )
            ordinal += 1
    return {
        "schema_version": SOURCE_SCHEMA,
        "source": {
            "content_address_key": "a" * 64,
            "bundle_run_id": "autoresearch-score-handoff-test-v1",
            "bundle_created_at": "2026-08-28T21:01:45Z",
            "bundle_storage_uri": f"ssh://example/artifacts/score-all/{'a' * 64}/",
            "bundle_receipt_sha256": "b" * 64,
            "bundle_receipt_size_bytes": 100,
            "manifest_sha256": "c" * 64,
            "manifest_size_bytes": 200,
            "strict_library_sha256": "d" * 64,
            "strict_library_size_bytes": 300,
        },
        "rows": rows,
    }


def test_selects_six_globally_unique_fifty_family_cohorts() -> None:
    source, rows = parse_strict_source(_source_document())
    excluded_sequence = rows[0].sequence_sha256
    excluded_family = rows[52].family_key_80_80

    selected, stats, order = select_fresh_rows(
        rows,
        excluded_sequence_sha256s={excluded_sequence},
        excluded_family_keys={excluded_family},
    )

    assert set(order) == set(TARGET_KEYS)
    assert all(len(selected[target]) == 50 for target in TARGET_KEYS)
    assert sum(len(items) for items in selected.values()) == 300
    assert len({row.sequence_sha256 for items in selected.values() for row in items}) == 300
    assert len({row.family_key_80_80 for items in selected.values() for row in items}) == 300
    assert all(stats[target]["shortfall"] == 0 for target in TARGET_KEYS)
    assert reservation_key(source) == reservation_key(source)


def test_cross_target_family_collision_is_not_selected_twice() -> None:
    payload = _source_document(per_target=52)
    rows = payload["rows"]
    assert isinstance(rows, list)
    rows[52]["family_key_80_80"] = rows[0]["family_key_80_80"]
    _, parsed = parse_strict_source(payload)

    selected, _, _ = select_fresh_rows(
        parsed,
        excluded_sequence_sha256s=set(),
        excluded_family_keys=set(),
    )

    families = [row.family_key_80_80 for items in selected.values() for row in items]
    assert len(families) == len(set(families)) == 300


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("toxinpred3_label", None),
        ("macrel_hemolysis_label", None),
        ("guruprasad_instability_ood", True),
        ("activity_model_support_count", 1),
        ("strict_display_eligible", False),
    ],
)
def test_literal_eligibility_fields_fail_closed(field: str, value: object) -> None:
    payload = _source_document()
    rows = payload["rows"]
    assert isinstance(rows, list)
    rows[0][field] = value

    with pytest.raises(ValueError):
        parse_strict_source(payload)


def test_shortfall_fails_before_any_reservation() -> None:
    payload = _source_document(per_target=50)
    _, rows = parse_strict_source(payload)
    first_target = TARGET_KEYS[0]
    excluded = next(
        row.family_key_80_80 for row in rows if row.target_key == first_target
    )

    with pytest.raises(ValueError, match="shortfall=1"):
        select_fresh_rows(
            rows,
            excluded_sequence_sha256s=set(),
            excluded_family_keys={excluded},
        )


def test_reservation_key_changes_with_published_source_identity() -> None:
    payload = _source_document()
    source, _ = parse_strict_source(payload)
    changed = copy.deepcopy(payload)
    changed["source"]["strict_library_sha256"] = "e" * 64
    changed_source, _ = parse_strict_source(changed)

    assert reservation_key(source) != reservation_key(changed_source)
