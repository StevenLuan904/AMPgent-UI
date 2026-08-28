from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    operational_run_id,
)


def _record(**updates: object) -> OperationalCallRecord:
    payload: dict[str, object] = {
        "operation_key": "generation:.32:gpu0:pepmlm-vegfa-v125",
        "target_key": "vegfa",
        "purpose": "generation",
        "tool_name": "pepmlm-generation",
        "tool_version": "1",
        "status": "succeeded",
        "input_payload": {"requested_count": 768, "seed": 20260829},
        "execution_context": {"host": "192.168.99.32", "gpu": 0, "pid": 1044416},
        "output_payload": {"raw_count": 768, "valid_count": 768},
        "started_at": datetime(2026, 8, 29, 1, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 8, 29, 1, 5, tzinfo=UTC),
    }
    payload.update(updates)
    return OperationalCallRecord.model_validate(payload)


def test_operational_run_identity_is_stable_and_target_scoped() -> None:
    first = _record()
    same = _record(output_payload={"raw_count": 768, "valid_count": 768})
    other_target = _record(target_key="fgf2")

    assert operational_run_id(first) == operational_run_id(same)
    assert operational_run_id(first) != operational_run_id(other_target)


def test_operational_terminal_contract_rejects_missing_output() -> None:
    with pytest.raises(ValidationError, match="requires output"):
        _record(output_payload=None)


def test_running_call_cannot_claim_terminal_fields() -> None:
    with pytest.raises(ValidationError, match="cannot carry a terminal payload"):
        _record(status="running")


def test_activity_reconciliation_is_a_supported_operational_purpose() -> None:
    record = _record(
        purpose="audit_reconciliation",
        tool_name="temporal-activity-pg-reconciler",
    )

    assert record.purpose == "audit_reconciliation"
