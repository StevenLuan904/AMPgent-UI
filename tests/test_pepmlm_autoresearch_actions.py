from __future__ import annotations

import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")

from pepagent.model_workers import pepmlm_cli


def _action() -> dict[str, object]:
    return {
        "action_id": "fgf2-denovo-0001",
        "action_kind": "de_novo",
        "seed": 100,
        "peptide_length": 12,
        "expected_improvement_axes": ["target_conditioning"],
        "protected_axes": ["instability_index_lt_50"],
    }


def test_action_retry_uses_deterministic_seeds_and_preserves_frozen_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[int | None, int, str]] = []

    def fake_execute(*args: object, **kwargs: object) -> dict[str, object]:
        action = args[3]
        assert isinstance(action, dict)
        observed.append(
            (
                kwargs["sampling_seed"],
                kwargs["sampling_attempt"],
                str(action["action_sha256"]),
            )
        )
        if len(observed) == 1:
            raise ValueError("non-canonical X")
        return {
            "sequence": "KLLKLLKKLLKL",
            "proposal_mode": "de_novo",
            "sampling_seed": kwargs["sampling_seed"],
            "sampling_attempt": kwargs["sampling_attempt"],
        }

    monkeypatch.setattr(pepmlm_cli, "execute_action", fake_execute)
    seen: set[str] = set()
    result = pepmlm_cli.execute_unique_action_with_retry(
        object(),
        object(),
        "TARGET",
        _action(),
        default_top_k=5,
        default_temperature=1.0,
        seen=seen,
        max_attempts=3,
    )

    assert [item[:2] for item in observed] == [(100, 0), (101, 1)]
    assert observed[0][2] == observed[1][2]
    assert result["sampling_seed"] == 101
    assert seen == {"KLLKLLKKLLKL"}


def test_action_retry_skips_duplicates() -> None:
    original = pepmlm_cli.execute_action
    calls = 0

    def fake_execute(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "sequence": "AAAAAAAAAA" if calls == 1 else "KKKKKKKKKK",
            "proposal_mode": "de_novo",
            "sampling_seed": kwargs["sampling_seed"],
            "sampling_attempt": kwargs["sampling_attempt"],
        }

    pepmlm_cli.execute_action = fake_execute
    try:
        seen = {"AAAAAAAAAA"}
        result = pepmlm_cli.execute_unique_action_with_retry(
            object(),
            object(),
            "TARGET",
            _action(),
            default_top_k=5,
            default_temperature=1.0,
            seen=seen,
            max_attempts=3,
        )
    finally:
        pepmlm_cli.execute_action = original

    assert result["sequence"] == "KKKKKKKKKK"
    assert calls == 2
    assert seen == {"AAAAAAAAAA", "KKKKKKKKKK"}
