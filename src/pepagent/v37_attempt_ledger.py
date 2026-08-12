from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import select, text

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


def _attempt_lock_id(context: V37AttemptContext) -> int:
    digest = bytes.fromhex(
        sha256_json(
            {
                "run_id": str(context.run_id),
                "v37_logical_id": context.logical_id,
                "activity_name": context.activity_name,
            }
        )
    )
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def _lock_attempt_lineage(session: Any, context: V37AttemptContext) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _attempt_lock_id(context)},
    )


async def _persist_attempt_event(
    context: V37AttemptContext, event_type: str, payload: dict[str, Any]
) -> None:
    async with SessionFactory() as session, session.begin():
        await _lock_attempt_lineage(session, context)
        if event_type in {"v37.attempt_failed", "v37.attempt_succeeded"}:
            lineage = list(
                await session.scalars(
                    select(LifecycleEvent).where(
                        LifecycleEvent.aggregate_type == "v37_attempt",
                        LifecycleEvent.payload_json["run_id"].astext
                        == str(context.run_id),
                        LifecycleEvent.payload_json["v37_logical_id"].astext
                        == context.logical_id,
                        LifecycleEvent.payload_json["activity_name"].astext
                        == context.activity_name,
                    )
                )
            )
            if any(
                (
                    row.event_type == "v37.attempt_interrupted"
                    and int(row.payload_json["attempt"]) == context.attempt
                )
                or (
                    row.event_type == "v37.attempt_started"
                    and int(row.payload_json["attempt"]) > context.attempt
                )
                for row in lineage
            ):
                raise RuntimeError("v37 attempt was superseded by a later Temporal retry")
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


async def _begin_database_attempt(
    context: V37AttemptContext, identity: dict[str, Any]
) -> None:
    async with SessionFactory() as session, session.begin():
        await _lock_attempt_lineage(session, context)
        rows = list(
            await session.scalars(
                select(LifecycleEvent).where(
                    LifecycleEvent.aggregate_type == "v37_attempt",
                    LifecycleEvent.payload_json["run_id"].astext == str(context.run_id),
                    LifecycleEvent.payload_json["v37_logical_id"].astext
                    == context.logical_id,
                    LifecycleEvent.payload_json["activity_name"].astext
                    == context.activity_name,
                )
            )
        )
        by_attempt: dict[int, list[LifecycleEvent]] = {}
        for row in rows:
            by_attempt.setdefault(int(row.payload_json["attempt"]), []).append(row)
        current = by_attempt.get(context.attempt, [])
        current_starts = [
            row for row in current if row.event_type == "v37.attempt_started"
        ]
        if current_starts:
            expected = {**identity, "status": "started"}
            if len(current_starts) != 1 or current_starts[0].payload_json != expected:
                raise ValueError("persisted v37 attempt start differs on retry")
            return
        if any(
            row.event_type == "v37.attempt_succeeded"
            for attempt, prior in by_attempt.items()
            if attempt < context.attempt
            for row in prior
        ):
            raise ValueError("v37 retry started after a prior attempt succeeded")
        await ExperimentRepository(session).append_event(
            "v37_attempt",
            context.aggregate_id,
            "v37.attempt_started",
            "v37-formal-runtime",
            {**identity, "status": "started"},
        )
        for attempt in range(1, context.attempt):
            prior = by_attempt.get(attempt, [])
            has_start = any(row.event_type == "v37.attempt_started" for row in prior)
            has_terminal = any(
                row.event_type
                in {
                    "v37.attempt_failed",
                    "v37.attempt_succeeded",
                    "v37.attempt_interrupted",
                }
                for row in prior
            )
            if not has_start or has_terminal:
                continue
            prior_context = V37AttemptContext(
                context.run_id,
                context.logical_id,
                context.activity_name,
                attempt,
            )
            await ExperimentRepository(session).append_event(
                "v37_attempt",
                prior_context.aggregate_id,
                "v37.attempt_interrupted",
                "v37-formal-runtime",
                {
                    "schema_version": "v37.attempt-event.1",
                    "run_id": str(context.run_id),
                    "v37_logical_id": context.logical_id,
                    "activity_name": context.activity_name,
                    "attempt": attempt,
                    "status": "interrupted",
                    "reason": "superseded_by_temporal_retry",
                    "observed_by_attempt": context.attempt,
                },
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
    database_backed = event_writer is _persist_attempt_event
    if database_backed:
        await _begin_database_attempt(context, identity)
    else:
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
    starts: dict[int, Mapping[str, Any]] = {}
    terminals: dict[int, Mapping[str, Any]] = {}
    interrupted: dict[int, Mapping[str, Any]] = {}
    lineage_identity: tuple[str, str] | None = None
    for item in relevant:
        payload = item["payload_json"]
        if (
            payload.get("schema_version") != "v37.attempt-event.1"
            or payload.get("v37_logical_id") != logical_id
            or not payload.get("run_id")
            or not payload.get("activity_name")
        ):
            raise ValueError("v37 attempt lifecycle identity is invalid")
        event_identity = (str(payload["run_id"]), str(payload["activity_name"]))
        if lineage_identity is None:
            lineage_identity = event_identity
        elif event_identity != lineage_identity:
            raise ValueError("v37 attempt lifecycle identity is inconsistent")
        attempt = int(payload["attempt"])
        if item.get("event_type") == "v37.attempt_started":
            if payload.get("status") != "started":
                raise ValueError("v37 attempt start lifecycle event is invalid")
            if attempt in starts:
                raise ValueError("v37 attempt has multiple start lifecycle events")
            starts[attempt] = payload
            continue
        if item.get("event_type") == "v37.attempt_interrupted":
            if attempt in interrupted:
                raise ValueError("v37 attempt has multiple interruption lifecycle events")
            if (
                payload.get("status") != "interrupted"
                or payload.get("reason") != "superseded_by_temporal_retry"
                or int(payload.get("observed_by_attempt", 0)) <= attempt
            ):
                raise ValueError("v37 attempt interruption lifecycle event is invalid")
            interrupted[attempt] = payload
            continue
        if item.get("event_type") not in {
            "v37.attempt_failed",
            "v37.attempt_succeeded",
        }:
            continue
        expected_status = (
            "failed" if item.get("event_type") == "v37.attempt_failed" else "succeeded"
        )
        if payload.get("status") != expected_status:
            raise ValueError("v37 attempt terminal lifecycle event is invalid")
        if expected_status == "failed" and (
            not payload.get("error_type") or not payload.get("error_sha256")
        ):
            raise ValueError("v37 attempt failure lifecycle event is invalid")
        if expected_status == "succeeded" and not payload.get("output_sha256"):
            raise ValueError("v37 attempt success lifecycle event is invalid")
        if attempt in terminals:
            raise ValueError("v37 attempt has multiple terminal lifecycle events")
        terminals[attempt] = payload
    if not terminals:
        raise ValueError("v37 durable attempt ledger lacks terminal success")
    if set(interrupted) & set(terminals):
        raise ValueError("v37 attempt has conflicting terminal lifecycle events")
    terminal_attempt = max({*starts, *terminals, *interrupted})
    if (
        terminal_attempt not in terminals
        or terminals[terminal_attempt]["status"] != "succeeded"
    ):
        raise ValueError("v37 durable attempt ledger lacks terminal success")
    attempts = []
    failures = []
    interruption_rows = []
    for attempt in range(1, terminal_attempt + 1):
        if attempt not in starts:
            raise ValueError("v37 durable attempt ledger is not contiguous")
        payload = terminals.get(attempt)
        interruption = interrupted.get(attempt)
        if payload is None and interruption is None:
            raise ValueError("v37 durable attempt ledger is not contiguous")
        if interruption is not None:
            if int(interruption["observed_by_attempt"]) not in starts:
                raise ValueError("v37 interruption lacks a persisted later-attempt witness")
            attempts.append({"attempt": attempt, "status": "interrupted"})
            interruption_rows.append(
                {
                    "attempt": attempt,
                    "reason": interruption["reason"],
                    "observed_by_attempt": int(interruption["observed_by_attempt"]),
                }
            )
        else:
            assert payload is not None
            attempts.append({"attempt": attempt, "status": payload["status"]})
        if payload is not None and payload["status"] == "failed":
            failures.append(
                {
                    "attempt": attempt,
                    "error_type": payload["error_type"],
                    "error_sha256": payload["error_sha256"],
                }
            )
    return {
        "attempt_ledger": {
            "schema_version": "1.0",
            "v37_logical_id": logical_id,
            "attempts": attempts,
            "interruptions": interruption_rows,
        },
        "failure_ledger": {
            "schema_version": "1.0",
            "v37_logical_id": logical_id,
            "failures": failures,
        },
    }
