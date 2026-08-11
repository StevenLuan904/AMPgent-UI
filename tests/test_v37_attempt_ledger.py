from __future__ import annotations

import asyncio
import uuid

import pytest

from pepagent.v37_attempt_ledger import (
    V37AttemptContext,
    build_v37_attempt_artifacts,
    execute_v37_durable_attempt,
    v37_error_is_retryable,
)


def test_transient_failure_and_retry_success_are_both_durable() -> None:
    run_id = uuid.uuid4()
    events: list[dict[str, object]] = []

    async def writer(
        context: V37AttemptContext, event_type: str, payload: dict[str, object]
    ) -> None:
        events.append({"event_type": event_type, "payload_json": payload})

    async def fail() -> dict[str, object]:
        raise RuntimeError("temporary provider timeout")

    async def succeed() -> dict[str, object]:
        return {"records": 1000}

    first = V37AttemptContext(run_id, "v37:generation:hydramp:20270371", "generate_v37_batch", 1)
    second = V37AttemptContext(run_id, first.logical_id, first.activity_name, 2)
    with pytest.raises(RuntimeError, match="temporary"):
        asyncio.run(execute_v37_durable_attempt(fail, context=first, event_writer=writer))
    assert asyncio.run(
        execute_v37_durable_attempt(succeed, context=second, event_writer=writer)
    ) == {"records": 1000}
    artifacts = build_v37_attempt_artifacts(events, logical_id=first.logical_id)
    assert artifacts["attempt_ledger"]["attempts"] == [
        {"attempt": 1, "status": "failed"},
        {"attempt": 2, "status": "succeeded"},
    ]
    assert artifacts["failure_ledger"]["failures"][0]["error_type"] == "RuntimeError"


@pytest.mark.parametrize("error", [ValueError("bad"), KeyError("missing"), TypeError("wrong")])
def test_provenance_and_contract_errors_are_non_retryable(error: BaseException) -> None:
    assert v37_error_is_retryable(error) is False


def test_transient_runtime_error_is_retryable() -> None:
    assert v37_error_is_retryable(RuntimeError("temporary")) is True
