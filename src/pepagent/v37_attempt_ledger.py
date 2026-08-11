from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import select

from pepagent.db.models import LifecycleEvent
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_json

T = TypeVar("T")
V37_NON_RETRYABLE_ERRORS = (ValueError, KeyError, TypeError)


@dataclass(frozen=True)
class V37AttemptContext:
    run_id: uuid.UUID
    logical_id: str
    activity_name: str
    attempt: int

    def __post_init__(self) -> None:
        if not self.logical_id.startswith("v37:"):
            raise ValueError("v37 attempt logical_id is invalid")
        if not self.activity_name or self.attempt < 1:
            raise ValueError("v37 attempt identity is invalid")

    @property
    def aggregate_id(self) -> uuid.UUID:
        return uuid.uuid5(
            self.run_id,
            f"{self.logical_id}:{self.activity_name}:attempt:{self.attempt}",
        )


def v37_error_is_retryable(error: BaseException) -> bool:
    return not isinstance(error, V37_NON_RETRYABLE_ERRORS)


async def _persist_attempt_event(
    context: V37AttemptContext, event_type: str, payload: dict[str, Any]
) -> None:
    async with SessionFactory() as session, session.begin():
        existing = await session.scalar(
            select(LifecycleEvent).where(
                LifecycleEvent.aggregate_type == "v37_attempt",
                LifecycleEvent.aggregate_id == context.aggregate_id,
                LifecycleEvent.event_type == event_type,
            )
        )
        if existing is not None:
            if existing.payload_json != payload:
                raise ValueError("persisted v37 attempt event differs on retry")
            return
        await ExperimentRepository(session).append_event(
            "v37_attempt",
            context.aggregate_id,
            event_type,
            "v37-formal-runtime",
            payload,
        )


async def execute_v37_durable_attempt(
    operation: Callable[[], Awaitable[T]],
    *,
    context: V37AttemptContext,
    event_writer: Callable[
        [V37AttemptContext, str, dict[str, Any]], Awaitable[None]
    ] = _persist_attempt_event,
) -> T:
    """Persist start and terminal outcome in transactions outside activity work."""
    identity = {
        "schema_version": "v37.attempt-event.1",
        "run_id": str(context.run_id),
        "v37_logical_id": context.logical_id,
        "activity_name": context.activity_name,
        "attempt": context.attempt,
    }
    await event_writer(
        context,
        "v37.attempt_started",
        {**identity, "status": "started"},
    )
    try:
        result = await operation()
    except BaseException as error:
        error_payload = {
            "error_type": type(error).__name__,
            "message": str(error),
        }
        await event_writer(
            context,
            "v37.attempt_failed",
            {
                **identity,
                "status": "failed",
                "retryable": v37_error_is_retryable(error),
                "error_type": error_payload["error_type"],
                "error_sha256": sha256_json(error_payload),
            },
        )
        raise
    await event_writer(
        context,
        "v37.attempt_succeeded",
        {
            **identity,
            "status": "succeeded",
            "output_sha256": sha256_json(result),
        },
    )
    return result


def build_v37_attempt_artifacts(
    events: Sequence[Mapping[str, Any]], *, logical_id: str
) -> dict[str, dict[str, Any]]:
    """Project durable lifecycle rows into the frozen replay artifact contract."""
    relevant = [
        item for item in events if item.get("payload_json", {}).get("v37_logical_id") == logical_id
    ]
    terminals: dict[int, Mapping[str, Any]] = {}
    for item in relevant:
        if item.get("event_type") not in {
            "v37.attempt_failed",
            "v37.attempt_succeeded",
        }:
            continue
        payload = item["payload_json"]
        attempt = int(payload["attempt"])
        if attempt in terminals:
            raise ValueError("v37 attempt has multiple terminal lifecycle events")
        terminals[attempt] = payload
    attempts = []
    failures = []
    for attempt in sorted(terminals):
        payload = terminals[attempt]
        attempts.append({"attempt": attempt, "status": payload["status"]})
        if payload["status"] == "failed":
            failures.append(
                {
                    "attempt": attempt,
                    "error_type": payload["error_type"],
                    "error_sha256": payload["error_sha256"],
                }
            )
    if [item["attempt"] for item in attempts] != list(range(1, len(attempts) + 1)):
        raise ValueError("v37 durable attempt ledger is not contiguous")
    if not attempts or attempts[-1]["status"] != "succeeded":
        raise ValueError("v37 durable attempt ledger lacks terminal success")
    return {
        "attempt_ledger": {
            "schema_version": "1.0",
            "v37_logical_id": logical_id,
            "attempts": attempts,
        },
        "failure_ledger": {
            "schema_version": "1.0",
            "v37_logical_id": logical_id,
            "failures": failures,
        },
    }
