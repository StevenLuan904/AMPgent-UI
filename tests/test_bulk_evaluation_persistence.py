from __future__ import annotations

import uuid
from typing import Any

import pytest

from pepagent.db.models import Evaluation
from pepagent.db.repository import ExperimentRepository
from pepagent.domain.enums import EvaluationStatus


class _BulkSession:
    def __init__(self, existing: list[Evaluation] | None = None) -> None:
        self.existing = existing or []
        self.added: list[Evaluation] = []
        self.flush_count = 0
        self.scalar_queries = 0

    async def scalars(self, _query: Any) -> list[Evaluation]:
        self.scalar_queries += 1
        return self.existing

    def add(self, row: Evaluation) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flush_count += 1


def _row(candidate_id: uuid.UUID, metric_name: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "metric_name": metric_name,
        "numeric_value": 1.25,
        "text_value": None,
        "unit": "log10_um",
        "out_of_domain": False,
        "limitations": [],
        "raw": {"score": 1.25},
    }


@pytest.mark.asyncio
async def test_record_evaluations_bulk_uses_one_read_and_one_flush() -> None:
    tool_call_id = uuid.uuid4()
    rows = [_row(uuid.uuid4(), "metric_a") for _ in range(100)]
    session = _BulkSession()

    observed = await ExperimentRepository(session).record_evaluations_bulk(  # type: ignore[arg-type]
        tool_call_id, rows
    )

    assert len(observed) == 100
    assert session.scalar_queries == 1
    assert session.flush_count == 1
    assert len(session.added) == 100
    assert all(item.status == EvaluationStatus.SUCCEEDED for item in observed)


@pytest.mark.asyncio
async def test_record_evaluations_bulk_is_idempotent_for_existing_evidence() -> None:
    tool_call_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    existing = Evaluation(
        id=uuid.uuid4(),
        candidate_id=candidate_id,
        tool_call_id=tool_call_id,
        metric_name="metric_a",
        numeric_value=1.25,
        text_value=None,
        unit="log10_um",
        status=EvaluationStatus.SUCCEEDED,
        out_of_domain=False,
        limitations_json=[],
        raw_json={"score": 1.25},
    )
    session = _BulkSession([existing])

    observed = await ExperimentRepository(session).record_evaluations_bulk(  # type: ignore[arg-type]
        tool_call_id, [_row(candidate_id, "metric_a")]
    )

    assert observed == [existing]
    assert session.scalar_queries == 1
    assert session.flush_count == 0
    assert session.added == []


@pytest.mark.asyncio
async def test_record_evaluations_bulk_rejects_duplicate_evidence_identity() -> None:
    tool_call_id = uuid.uuid4()
    row = _row(uuid.uuid4(), "metric_a")
    session = _BulkSession()

    with pytest.raises(ValueError, match="duplicate evidence identities"):
        await ExperimentRepository(session).record_evaluations_bulk(  # type: ignore[arg-type]
            tool_call_id, [row, dict(row)]
        )

    assert session.scalar_queries == 0
    assert session.flush_count == 0
