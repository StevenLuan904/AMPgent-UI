from copy import deepcopy

import pytest

from pepagent.model_workers.amp_designer_generator_v38_cli import _validate_request


def _request() -> dict:
    return {
        "schema_version": "v38.generator-request.1",
        "generator_id": "amp_designer",
        "seed": 20270377,
        "device": "cpu",
        "raw_proposal_budget": 100,
        "batch_size": 100,
        "batches": 1,
        "top_k": 10,
        "top_p": 1.0,
        "temperature": None,
        "decode_steps": 34,
    }


def test_v38_amp_designer_accepts_exactly_one_frozen_batch() -> None:
    assert _validate_request(_request()) == (20270377, "cpu")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "v37.generator-request.1"),
        ("raw_proposal_budget", 1000),
        ("batch_size", 50),
        ("batches", 10),
        ("top_k", 20),
        ("top_p", 0.9),
        ("temperature", 1.0),
        ("decode_steps", 33),
        ("device", "auto"),
    ],
)
def test_v38_amp_designer_fails_closed_on_contract_drift(
    field: str, value: object
) -> None:
    request = deepcopy(_request())
    request[field] = value
    with pytest.raises(ValueError):
        _validate_request(request)
