from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.resume_angpt1_pepmlm_qd_neighbor_v2b import (
    PostgresRuntimeUnavailable,
    load_manifest,
    resume,
)

ROOT = Path(__file__).parents[1]
REPORT = ROOT / "reports/angpt1_pepmlm_qd_neighbor_v2b_20260904"


def _sequences() -> dict[str, str]:
    with (REPORT / "materialization_inputs/candidate_scores.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        return {row["sequence_sha256"]: row["sequence"] for row in csv.DictReader(stream)}


def test_load_manifest_verifies_two_qd_contributor_identities_and_hashes() -> None:
    manifest = load_manifest(REPORT)
    assert len(manifest.sequence_sha256s) == 2
    assert manifest.expected_run_id
    assert manifest.score_sha256
    assert set(manifest.sequence_sha256s) == set(_sequences())


def test_pg_unavailable_is_zero_write_and_skips_materializer(tmp_path: Path) -> None:
    calls: list[Any] = []

    async def unavailable(_hashes: Any, _run_id: Any) -> dict[str, Any]:
        raise PostgresRuntimeUnavailable("connection refused")

    async def materializer(_args: Any) -> dict[str, Any]:
        calls.append(True)
        return {}

    result = asyncio.run(
        resume(
            REPORT,
            existing_lookup=unavailable,
            materializer=materializer,
            output_dir=tmp_path,
        )
    )
    assert result["status"] == "runtime_unavailable"
    assert result["pg_write_count"] == 0
    assert result["materializer_called"] is False
    assert result["coarse5_prepared_count"] == 0
    assert calls == []


def test_all_exact_candidates_are_reused_for_prepared_coarse5(tmp_path: Path) -> None:
    manifest = load_manifest(REPORT)
    sequences = _sequences()
    existing = {
        digest: SimpleNamespace(
            run_id=manifest.expected_run_id,
            sequence=sequences[digest],
            sequence_sha256=digest,
        )
        for digest in manifest.sequence_sha256s
    }
    calls: list[Any] = []

    async def lookup(_hashes: Any, _run_id: Any) -> dict[str, Any]:
        return existing

    async def materializer(_args: Any) -> dict[str, Any]:
        calls.append(True)
        return {}

    materialization_path = tmp_path / "materialization_receipt.json"
    materialization_path.write_text(
        json.dumps({"operational_run_id": str(manifest.expected_run_id)}),
        encoding="utf-8",
    )

    def coarse_builder(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["materialization_json"] == materialization_path
        return {
            "run_id": str(manifest.expected_run_id),
            "candidate_count": 2,
            "nstruct": 5,
            "dispatch_allowed": False,
        }

    result = asyncio.run(
        resume(
            REPORT,
            existing_lookup=lookup,
            materializer=materializer,
            coarse_builder=coarse_builder,
            output_dir=tmp_path,
        )
    )
    assert result["status"] == "already_materialized"
    assert result["decision"] == "prepared_not_dispatched"
    assert result["materializer_called"] is False
    assert result["coarse5_prepared_count"] == 2
    assert result["nstruct"] == 5
    assert result["dispatch_allowed"] is False
    assert calls == []


def test_recovery_delegates_once_then_requires_uuid_bound_coarse5(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(REPORT)
    calls: list[Any] = []

    async def lookup(_hashes: Any, _run_id: Any) -> dict[str, Any]:
        return {}

    async def materializer(args: Any) -> dict[str, Any]:
        calls.append(args)
        return {
            "operational_run_id": str(manifest.expected_run_id),
            "materialized_or_reused_in_run_count": 2,
            "inserted_evaluation_count": 34,
            "tool_call_id": "c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1",
            "historical_runs_modified": False,
        }

    def coarse_builder(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["qd_json"].name == "qd_summary.json"
        materialization = json.loads(kwargs["materialization_json"].read_text())
        assert materialization["operational_run_id"] == str(manifest.expected_run_id)
        return {
            "run_id": str(manifest.expected_run_id),
            "candidate_count": 2,
            "nstruct": 5,
            "dispatch_allowed": False,
        }

    result = asyncio.run(
        resume(
            REPORT,
            existing_lookup=lookup,
            materializer=materializer,
            coarse_builder=coarse_builder,
            output_dir=tmp_path,
        )
    )
    assert result["status"] == "materialized_and_coarse5_prepared"
    assert result["operational_run_id"] == str(manifest.expected_run_id)
    assert result["coarse5_prepared_count"] == 2
    assert result["nstruct"] == 5
    assert result["dispatch_allowed"] is False
    assert len(calls) == 1
    assert calls[0].source_receipt == REPORT / "close_receipt.json"
