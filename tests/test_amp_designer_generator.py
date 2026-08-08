from __future__ import annotations

from copy import deepcopy

import pytest

from pepagent.model_workers.amp_designer_generator_cli import (
    _decode_token_strings,
    _validate_request,
)


def _request() -> dict:
    return {
        "generator_id": "amp_designer",
        "seed": 20260825,
        "device": "cpu",
        "raw_proposal_budget": 1000,
        "batch_size": 100,
        "batches": 10,
        "top_k": 10,
        "top_p": 1.0,
        "temperature": None,
        "decode_steps": 34,
    }


def test_amp_designer_request_accepts_only_frozen_sampling_contract() -> None:
    assert _validate_request(_request()) == (20260825, "cpu")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("raw_proposal_budget", 999, "raw_proposal_budget"),
        ("batch_size", 50, "batch_size"),
        ("batches", 20, "batches"),
        ("top_k", 20, "top_k"),
        ("top_p", 0.9, "top_p"),
        ("temperature", 1.0, "temperature"),
        ("decode_steps", 33, "decode_steps"),
        ("device", "auto", "device"),
    ],
)
def test_amp_designer_request_fails_closed_on_sampling_drift(
    field: str,
    value: object,
    message: str,
) -> None:
    request = deepcopy(_request())
    request[field] = value
    with pytest.raises(ValueError, match=message):
        _validate_request(request)


def test_amp_designer_decoder_preserves_tokens_and_stops_at_first_sep() -> None:
    sequence, first_sep = _decode_token_strings(["a", "c", "[SEP]", "w"])
    assert sequence == "AC"
    assert first_sep == 3


def test_amp_designer_decoder_records_missing_sep_without_truncation() -> None:
    sequence, first_sep = _decode_token_strings(["a", "c", "w"])
    assert sequence == "ACW"
    assert first_sep is None
