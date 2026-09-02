from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "analysis" / "autoresearch_lineage_probe.py"
    spec = importlib.util.spec_from_file_location("_lineage_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_targeted_lineage_probe_accepts_selected_branch_only() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "analysis" / "autoresearch_lineage_probe.py"
    ).read_text(encoding="utf-8")

    assert "source_branches != set(active_branches)" in source
    assert "requires all six target branches" not in source
    assert "args.minimum_calibrated_support" in source
    assert 'args.output_dir / "source_cohort.csv"' in source
    assert "historical_sequences = set(input_sequences)" in source
    assert "args.include_postgresql_history" in source
    assert "sequence_hashes.update(postgresql_hashes)" in source
    assert "candidate_hashes | operational_hashes" in source
    assert '"unchecked"' in source
    assert '"display_or_promotion_allowed"' in source


def test_offline_lineage_generation_is_fail_closed_for_display_and_promotion() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "analysis" / "autoresearch_lineage_probe.py"
    ).read_text(encoding="utf-8")

    assert 'history_check_status = "deferred_to_postgresql_materialization_gate"' in source
    assert "display_or_promotion_allowed = False" in source
    assert '"false" if display_or_promotion_allowed else "unchecked"' in source
    assert '"display_or_promotion_allowed": display_or_promotion_allowed' in source


def test_operational_score_history_is_included_in_replay_exclusion() -> None:
    module = _load_module()
    score_hash = "a" * 64
    ignored_hash = "b" * 64

    observed = module._operational_score_sequence_hashes(
        [
            {
                "purpose": "score_all",
                "status": "succeeded",
                "output": {"candidates": [{"sequence_sha256": score_hash}]},
            },
            {
                "purpose": "challenger",
                "status": "succeeded",
                "output": {"candidates": [{"sequence_sha256": ignored_hash}]},
            },
            {
                "purpose": "score_all",
                "status": "failed",
                "output": {"candidates": [{"sequence_sha256": ignored_hash}]},
            },
        ]
    )

    assert observed == {score_hash}


def test_malformed_operational_score_history_fails_closed() -> None:
    module = _load_module()

    try:
        module._operational_score_sequence_hashes(
            [
                {
                    "purpose": "score_all",
                    "status": "succeeded",
                    "output": {"candidates": [{}]},
                }
            ]
        )
    except ValueError as exc:
        assert "missing a sequence SHA-256" in str(exc)
    else:
        raise AssertionError("malformed operational history must fail closed")


def test_prefer_full_support_removes_weaker_rows_only_from_rescued_families() -> None:
    module = _load_module()
    rows = [
        {
            "family_key_80_80": "rescued",
            "activity_model_support_count_calibrated": "3",
            "sequence": "AAA",
        },
        {
            "family_key_80_80": "rescued",
            "activity_model_support_count_calibrated": "2",
            "sequence": "BBB",
        },
        {
            "family_key_80_80": "remaining",
            "activity_model_support_count_calibrated": "2",
            "sequence": "CCC",
        },
    ]

    selected = module._prefer_full_support_within_families(rows)

    assert [row["sequence"] for row in selected] == ["AAA", "CCC"]


def test_unresolved_family_parent_ids_are_complete_and_deterministic() -> None:
    module = _load_module()
    rows = [
        {
            "family_key_80_80": "gap-b",
            "activity_model_support_count_calibrated": "2",
            "sequence_sha256": "b" * 64,
        },
        {
            "family_key_80_80": "resolved",
            "activity_model_support_count_calibrated": "3",
            "sequence_sha256": "c" * 64,
        },
        {
            "family_key_80_80": "gap-a",
            "activity_model_support_count_calibrated": "2",
            "sequence_sha256": "a" * 64,
        },
        {
            "family_key_80_80": "resolved",
            "activity_model_support_count_calibrated": "2",
            "sequence_sha256": "d" * 64,
        },
    ]

    assert module._unresolved_family_parent_ids(rows) == {
        "gap-a": ("a" * 64,),
        "gap-b": ("b" * 64,),
    }


def test_de_novo_profile_prefers_three_model_qd_elites() -> None:
    module = _load_module()
    candidate_ids = tuple(str(index) for index in range(5))
    rows = {
        candidate_id: {
            "activity_model_support_count_calibrated": "3" if index < 3 else "2"
        }
        for index, candidate_id in enumerate(candidate_ids)
    }

    selected, preferred, source = module._de_novo_profile_parent_ids(candidate_ids, rows)

    assert selected == candidate_ids
    assert preferred == ("0", "1", "2")
    assert source == "all_quality_gated_qd_elites_with_three_model_family_priority"


def test_de_novo_profile_falls_back_when_full_support_is_sparse() -> None:
    module = _load_module()
    candidate_ids = ("a", "b", "c", "d", "e", "f", "g")
    rows = {
        "a": {"activity_model_support_count_calibrated": "3"},
        "b": {"activity_model_support_count_calibrated": "3"},
        "c": {"activity_model_support_count_calibrated": "3"},
        "d": {"activity_model_support_count_calibrated": "2"},
        "e": {"activity_model_support_count_calibrated": "2"},
        "f": {"activity_model_support_count_calibrated": "2"},
        "g": {"activity_model_support_count_calibrated": "2"},
    }

    selected, preferred, source = module._de_novo_profile_parent_ids(candidate_ids, rows)

    assert selected == candidate_ids
    assert preferred == ("a", "b", "c")
    assert source == "all_quality_gated_qd_elites_with_three_model_family_priority"
