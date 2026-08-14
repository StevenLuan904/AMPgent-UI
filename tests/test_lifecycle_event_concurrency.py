from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from pepagent.db.repository import ExperimentRepository, _lifecycle_event_lock_id


@dataclass
class _SequenceState:
    locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    rows: list[Any] = field(default_factory=list)


class _ConcurrentSession:
    """Deterministic transaction probe for aggregate-local event allocation."""

    def __init__(self, state: _SequenceState) -> None:
        self.state = state
        self.pending: Any | None = None
        self.lock: asyncio.Lock | None = None

    async def execute(self, statement: Any, parameters: dict[str, int]) -> None:
        assert str(statement) == "SELECT pg_advisory_xact_lock(:lock_id)"
        self.lock = self.state.locks.setdefault(parameters["lock_id"], asyncio.Lock())
        await self.lock.acquire()

    async def scalar(self, _query: Any) -> int | None:
        # Yield at the vulnerable read point so an implementation without the
        # aggregate lock deterministically exposes duplicate allocation.
        await asyncio.sleep(0)
        values = [int(row.sequence_no) for row in self.state.rows]
        return max(values) if values else None

    def add(self, event: Any) -> None:
        self.pending = event

    async def flush(self) -> None:
        await asyncio.sleep(0)
        assert self.pending is not None
        self.state.rows.append(self.pending)
        self.pending = None

    def commit_probe(self) -> None:
        assert self.lock is not None and self.lock.locked()
        self.lock.release()


def test_lifecycle_event_lock_key_is_stable_scoped_and_signed() -> None:
    aggregate_id = uuid.UUID("21f3f85d-bbbc-4bcc-aa8a-33b349189a86")
    observed = _lifecycle_event_lock_id("run", aggregate_id)

    assert observed == _lifecycle_event_lock_id("run", aggregate_id)
    assert observed != _lifecycle_event_lock_id("candidate", aggregate_id)
    assert observed != _lifecycle_event_lock_id("run", uuid.uuid4())
    assert -(2**63) <= observed < 2**63


@pytest.mark.asyncio
async def test_concurrent_lifecycle_appends_allocate_exact_monotonic_sequence() -> None:
    state = _SequenceState()
    aggregate_id = uuid.UUID("4322b1cc-c56c-4132-b45c-b3116c234f18")

    async def append(index: int) -> None:
        session = _ConcurrentSession(state)
        try:
            await ExperimentRepository(session).append_event(  # type: ignore[arg-type]
                "run",
                aggregate_id,
                "v37.metric_persisted",
                f"metric-worker-{index}",
                {"metric_ordinal": index},
            )
        finally:
            session.commit_probe()

    await asyncio.gather(*(append(index) for index in range(32)))

    ordered = sorted(state.rows, key=lambda row: row.sequence_no)
    assert [row.sequence_no for row in ordered] == list(range(1, 33))
    assert {row.payload_json["metric_ordinal"] for row in ordered} == set(range(32))
    assert len({row.payload_sha256 for row in ordered}) == 32
