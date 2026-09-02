from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import uuid
from pathlib import Path

import pytest

SOURCE_PATH = (
    Path(__file__).parents[1] / "deploy" / "remote" / "run_rosetta_receipt_ingester.py"
)
SPEC = importlib.util.spec_from_file_location("run_rosetta_receipt_ingester", SOURCE_PATH)
assert SPEC is not None and SPEC.loader is not None
INGESTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INGESTER)


def _receipt(tmp_path: Path, *, primary: float = -4.5) -> Path:
    candidate = tmp_path / "candidates" / "acea" / "sequence"
    result_path = candidate / "results" / "rosetta_result.json"
    result_path.parent.mkdir(parents=True)
    result = {
        "nstruct": 20,
        "decoys": [
            {"reweighted_sc": float(index), "dG_separated": -float(index)}
            for index in range(20)
        ],
    }
    result_path.write_text(json.dumps(result), encoding="utf-8")
    receipt_path = candidate / "completion_receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": (
                    "ampgent.autoresearch-rosetta-candidate-completion.1"
                ),
                "status": "succeeded",
                "candidate_id": str(uuid.uuid4()),
                "sequence_sha256": "a" * 64,
                "target_key": "acea",
                "nstruct": 20,
                "primary_dG_separated_reu": primary,
                "minimum_dG_separated_reu": -19.0,
                "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return receipt_path


def test_validate_receipt_recomputes_top10_median_and_minimum(tmp_path: Path) -> None:
    validated = INGESTER.validate_receipt(_receipt(tmp_path))

    assert validated["nstruct"] == 20
    assert validated["primary"] == -4.5
    assert validated["minimum"] == -19.0


def test_validate_receipt_rejects_primary_aggregation_drift(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="primary dG aggregation mismatch"):
        INGESTER.validate_receipt(_receipt(tmp_path, primary=-5.0))


def test_validate_bundle_item_preserves_remote_identity(tmp_path: Path) -> None:
    receipt_path = _receipt(tmp_path)
    result_path = receipt_path.parent / "results" / "rosetta_result.json"
    remote_path = "/sdd_data/pepagent/run/candidates/acea/id/completion_receipt.json"

    validated = INGESTER.validate_bundle_item(
        {
            "schema_version": "ampgent.rosetta-receipt-bundle-item.1",
            "receipt_path": remote_path,
            "receipt_json": receipt_path.read_text(encoding="utf-8"),
            "result_json": result_path.read_text(encoding="utf-8"),
        }
    )

    assert validated["receipt_path"] == remote_path
    assert validated["receipt_sha256"] == hashlib.sha256(
        receipt_path.read_bytes()
    ).hexdigest()


def test_validate_bundle_item_rejects_result_hash_drift(tmp_path: Path) -> None:
    receipt_path = _receipt(tmp_path)

    with pytest.raises(ValueError, match="result hash does not match receipt"):
        INGESTER.validate_bundle_item(
            {
                "schema_version": "ampgent.rosetta-receipt-bundle-item.1",
                "receipt_path": "/sdd_data/pepagent/completion_receipt.json",
                "receipt_json": receipt_path.read_text(encoding="utf-8"),
                "result_json": "{}",
            }
        )


def test_watcher_reuses_one_event_loop_across_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop_ids: list[int] = []

    class WatchComplete(Exception):
        pass

    async def fake_scan_once(roots: list[Path], source_sha: str) -> dict[str, object]:
        del roots, source_sha
        loop_ids.append(id(asyncio.get_running_loop()))
        if len(loop_ids) == 3:
            raise WatchComplete
        return {"scan": len(loop_ids)}

    async def no_wait(seconds: float) -> None:
        del seconds

    monkeypatch.setattr(INGESTER, "scan_once", fake_scan_once)
    monkeypatch.setattr(INGESTER.asyncio, "sleep", no_wait)

    with pytest.raises(WatchComplete):
        asyncio.run(
            INGESTER.watch_roots(
                [tmp_path], "a" * 64, tmp_path / "state.json", watch_seconds=1
            )
        )

    assert len(set(loop_ids)) == 1
