"""Exact-once recovery entry point for the blocked ANGPT1 v2b materialization.

This module is deliberately a thin recovery wrapper.  It validates the immutable
offline witness, checks PostgreSQL for the exact sequence identities, and then
delegates writes to the existing scored-lineage materializer.  It never reruns
score-all, challenger review, or QD selection.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:
    from analysis.materialize_autoresearch_scored_lineage import (
        _existing_candidates,
        _record,
    )
    from analysis.materialize_autoresearch_scored_lineage import (
        materialize as exact_materialize,
    )
except ModuleNotFoundError as error:
    if error.name != "analysis":
        raise
    from materialize_autoresearch_scored_lineage import (  # type: ignore[no-redef]
        _existing_candidates,
        _record,
    )
    from materialize_autoresearch_scored_lineage import (
        materialize as exact_materialize,
    )
from pepagent.autoresearch_operational_call import operational_run_id
from pepagent.provenance.hashing import sha256_text

SCHEMA_VERSION = "ampgent.angpt1-pepmlm-qd-neighbor-v2b-resume.1"
REPORT_NAME = "angpt1_pepmlm_qd_neighbor_v2b_20260904"
EXPECTED_CONTRIBUTION_TYPES = {"empty_cell", "incumbent_replacement"}


class PostgresRuntimeUnavailable(RuntimeError):
    """Raised by injected lookup functions when PostgreSQL cannot be reached."""


@dataclass(frozen=True)
class ResumeManifest:
    report_root: Path
    repo_root: Path
    integrity_path: Path
    close_path: Path
    blocked_path: Path
    materialization_scores_path: Path
    materialization_challenger_path: Path
    qd_summary_path: Path
    qd_candidates_path: Path
    source_commit: str
    operator_id: str
    sequence_sha256s: tuple[str, ...]
    score_sha256: str
    close_sha256: str
    expected_run_id: uuid.UUID


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _declared_path(repo_root: Path, report_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        resolved = path
    else:
        # Receipts use both report-relative and repository-relative paths.
        candidates = (report_root / path, repo_root / path)
        resolved = next((item for item in candidates if item.is_file()), candidates[0])
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def _verify_digest(path: Path, expected: str, field: str) -> str:
    digest = str(expected).strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(f"{field} is not a SHA-256 digest")
    actual = _sha256(path)
    if actual != digest:
        raise ValueError(f"{field} hash mismatch: {path}")
    return actual


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _sequence_identity(row: Mapping[str, str], *, field: str = "sequence") -> str:
    sequence = "".join(str(row.get(field) or "").split()).upper()
    digest = str(row.get("sequence_sha256") or "").strip().lower()
    if not sequence or sha256_text(sequence) != digest:
        raise ValueError("sequence identity drifted")
    return digest


def _expected_run_id(*, score_sha256: str, close_sha256: str, source_commit: str) -> uuid.UUID:
    # This mirrors the existing materializer's preflight record.  The operation
    # key intentionally excludes mutable result counts, so retries share one ID.
    record = _record(
        branch="angpt1",
        generation=1,
        score_sha256=score_sha256,
        source_receipt_sha256=close_sha256,
        source_commit=source_commit,
        candidate_count=2,
        accepted_count=0,
        duplicate_count=0,
        excellent_count=0,
        challenger_reviewed_count=0,
        challenger_no_conflict_count=0,
    )
    return operational_run_id(record)


def load_manifest(report_root: Path) -> ResumeManifest:
    """Read and verify the immutable v2b witness without touching PostgreSQL."""

    report_root = report_root.resolve()
    repo_root = report_root.parents[1] if report_root.parent.name == "reports" else report_root
    integrity_path = report_root / "integrity_preflight.json"
    close_path = report_root / "close_receipt.json"
    blocked_path = report_root / "materialization_blocked_receipt.json"
    integrity = _json(integrity_path)
    close = _json(close_path)
    blocked = _json(blocked_path)

    if close.get("status") != "closed_unmaterialized_pg_blocked":
        raise ValueError("resume requires the immutable closed PG-blocked receipt")
    if blocked.get("status") != "blocked" or blocked.get("attempted_once") is not True:
        raise ValueError("resume requires the original one-time blocked receipt")
    pg_exact = close.get("pg_exact")
    if not isinstance(pg_exact, dict) or pg_exact.get("preflight_status") != "blocked":
        raise ValueError("close receipt is not a PG preflight block")
    if pg_exact.get("run_id") is not None or pg_exact.get("candidate_count") != 0:
        raise ValueError("blocked receipt already contains materialization")
    if (
        blocked.get("pg_write_count") != 0
        or blocked.get("offline_witness_is_not_pg_evidence") is not True
    ):
        raise ValueError("blocked receipt does not prove zero PG writes")

    score_path = _declared_path(repo_root, report_root, integrity["score_all_verified"]["path"])
    calibration_path = _declared_path(repo_root, report_root, integrity["calibration"]["path"])
    challenger_path = _declared_path(repo_root, report_root, integrity["challenger"]["path"])
    qd_archive_path = _declared_path(repo_root, report_root, integrity["qd_archive"]["path"])
    _verify_digest(score_path, integrity["score_all_verified"]["sha256"], "score_all")
    _verify_digest(calibration_path, integrity["calibration"]["sha256"], "calibration")
    _verify_digest(challenger_path, integrity["challenger"]["sha256"], "challenger")
    _verify_digest(qd_archive_path, integrity["qd_archive"]["sha256"], "qd_archive")

    materialization_root = report_root / "materialization_inputs"
    materialization_scores_path = materialization_root / "candidate_scores.csv"
    materialization_challenger_path = materialization_root / "challenger_review.csv"
    qd_summary_path = report_root / "qd" / "qd_summary.json"
    qd_candidates_path = report_root / "qd" / "qd_candidates.csv"
    for path in (
        materialization_scores_path,
        materialization_challenger_path,
        qd_summary_path,
        qd_candidates_path,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    score_rows = _rows(materialization_scores_path)
    challenger_rows = _rows(materialization_challenger_path)
    qd_rows = _rows(qd_candidates_path)
    if len(score_rows) != 2 or len(challenger_rows) != 2:
        raise ValueError("v2b recovery input must contain exactly two candidates")
    score_ids = tuple(_sequence_identity(row) for row in score_rows)
    challenger_ids = {_sequence_identity(row) for row in challenger_rows}
    if set(score_ids) != challenger_ids or len(set(score_ids)) != 2:
        raise ValueError("score/challenger sequence identities do not match")

    qd_summary = _json(qd_summary_path)
    qd_contributions = qd_summary.get("contributions")
    if not isinstance(qd_contributions, list):
        raise ValueError("QD summary has no contribution list")
    expected_ids = set(str(item) for item in close["qd_contribution_sequence_sha256"])
    blocked_ids = set(str(item) for item in blocked["materialization_candidate_sequence_sha256"])
    if expected_ids != blocked_ids or expected_ids != set(score_ids) or len(expected_ids) != 2:
        raise ValueError("blocked/close/materialization sequence identities disagree")
    qd_contribution_ids = {
        str(item.get("candidate_id"))
        for item in qd_contributions
        if item.get("contribution") in EXPECTED_CONTRIBUTION_TYPES
    }
    if qd_contribution_ids != expected_ids:
        raise ValueError("materialization inputs are not the two QD contributors")
    qd_identity = {_sequence_identity(row): row.get("sequence", "") for row in qd_rows}
    if set(score_ids) - set(qd_identity):
        raise ValueError("QD candidate sequence identity is missing")
    _verify_digest(
        qd_summary_path,
        json.loads((report_root / "qd" / "qd_receipt.json").read_text(encoding="utf-8")).get(
            "qd_summary_sha256", ""
        ),
        "qd_summary",
    )
    qd_receipt = _json(report_root / "qd" / "qd_receipt.json")
    _verify_digest(qd_candidates_path, qd_receipt.get("qd_candidates_sha256", ""), "qd_candidates")

    source_commit = str(blocked.get("source_commit") or "").strip()
    if len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit):
        raise ValueError("blocked receipt source_commit must be a lowercase SHA-1")
    for witness in (integrity, close):
        declared_commit = str(witness.get("source_commit") or "").strip()
        if declared_commit and declared_commit != source_commit:
            raise ValueError("v2b receipts disagree on source commit")
    operator_id = str(integrity.get("operator_id") or close.get("operator_id") or "").strip()
    if not operator_id:
        raise ValueError("operator_id is missing")
    close_sha256 = _sha256(close_path)
    score_sha256 = _sha256(materialization_scores_path)
    return ResumeManifest(
        report_root=report_root,
        repo_root=repo_root,
        integrity_path=integrity_path,
        close_path=close_path,
        blocked_path=blocked_path,
        materialization_scores_path=materialization_scores_path,
        materialization_challenger_path=materialization_challenger_path,
        qd_summary_path=qd_summary_path,
        qd_candidates_path=qd_candidates_path,
        source_commit=source_commit,
        operator_id=operator_id,
        sequence_sha256s=tuple(sorted(score_ids)),
        score_sha256=score_sha256,
        close_sha256=close_sha256,
        expected_run_id=_expected_run_id(
            score_sha256=score_sha256,
            close_sha256=close_sha256,
            source_commit=source_commit,
        ),
    )


def _pg_unavailable(error: BaseException) -> bool:
    if isinstance(error, PostgresRuntimeUnavailable):
        return True
    module_name = type(error).__module__.lower()
    text = str(error).lower()
    return "asyncpg" in module_name or any(
        marker in text
        for marker in (
            "winerror 1225",
            "connection refused",
            "connection timed out",
            "could not connect",
            "connect call failed",
            "no route to host",
        )
    )


def _candidate_run_id(candidate: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(candidate.run_id))
    except (AttributeError, ValueError):
        return None


def _candidate_identity_matches(candidate: Any, digest: str) -> bool:
    sequence = "".join(str(getattr(candidate, "sequence", "")).split()).upper()
    return (
        str(getattr(candidate, "sequence_sha256", "")).lower() == digest
        and bool(sequence)
        and sha256_text(sequence) == digest
    )


async def _default_materializer(args: argparse.Namespace) -> dict[str, Any]:
    return await exact_materialize(args, execute=True)


def _default_coarse_builder(**kwargs: Any) -> dict[str, Any]:
    try:
        from analysis.build_angpt1_pepglad_coarse5_prepared import build
    except ModuleNotFoundError as error:
        if error.name != "analysis":
            raise
        from build_angpt1_pepglad_coarse5_prepared import build  # type: ignore[no-redef]

    return build(**kwargs)


async def _prepare_coarse5(
    coarse_builder: Callable[..., Mapping[str, Any]],
    *,
    manifest: ResumeManifest,
    materialization_path: Path,
    destination: Path,
    authoritative_candidates: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the synchronous PG-backed builder outside the active event loop."""

    kwargs: dict[str, Any] = {
        "score_csv": manifest.materialization_scores_path,
        "qd_json": manifest.qd_summary_path,
        "materialization_json": materialization_path,
        "output_dir": destination / "coarse5_prepared",
        "target_key": "angpt1",
        "source": "PepMLM-target-conditioned",
        "operator_id": manifest.operator_id,
    }
    if coarse_builder is _default_coarse_builder and authoritative_candidates is not None:
        kwargs["authoritative_candidates"] = authoritative_candidates
    return dict(await asyncio.to_thread(coarse_builder, **kwargs))


async def resume(
    report_root: Path,
    *,
    existing_lookup: Callable[[Sequence[str], uuid.UUID], Awaitable[Mapping[str, Any]]]
    | None = None,
    materializer: Callable[[argparse.Namespace], Awaitable[Mapping[str, Any]]] | None = None,
    coarse_builder: Callable[..., Mapping[str, Any]] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Resume v2b with exact-once lookup and no score/challenger/QD reruns."""

    manifest = load_manifest(report_root)
    lookup = existing_lookup or _existing_candidates
    materializer = materializer or _default_materializer
    coarse_builder = coarse_builder or _default_coarse_builder
    try:
        existing = dict(await lookup(manifest.sequence_sha256s, manifest.expected_run_id))
    except Exception as error:  # classify only connection-boundary errors
        if not _pg_unavailable(error):
            raise
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "runtime_unavailable",
            "decision": "no-op",
            "pg_write_count": 0,
            "materializer_called": False,
            "coarse5_prepared_count": 0,
            "dispatch_allowed": False,
            "source_run_modified": False,
            "sequence_sha256s": list(manifest.sequence_sha256s),
            "error_category": "postgresql_unavailable_before_exact_materialization",
        }

    if len(existing) == len(manifest.sequence_sha256s):
        if not all(
            digest in existing and _candidate_identity_matches(existing[digest], digest)
            for digest in manifest.sequence_sha256s
        ):
            raise ValueError("existing PostgreSQL candidate identity drifted")
        run_ids = {_candidate_run_id(existing[digest]) for digest in manifest.sequence_sha256s}
        if None in run_ids:
            raise ValueError("existing PostgreSQL candidate has no valid run identity")
        destination = (output_dir or manifest.report_root / "resume_v2b").resolve()
        materialization_path = destination / "materialization_receipt.json"
        if not materialization_path.is_file():
            raise ValueError("already-materialized recovery lacks its materialization receipt")
        coarse = await _prepare_coarse5(
            coarse_builder,
            manifest=manifest,
            materialization_path=materialization_path,
            destination=destination,
            authoritative_candidates=existing,
        )
        run_id = str(next(iter(run_ids)))
        if str(coarse.get("run_id")) != run_id or int(coarse.get("nstruct", 0)) != 5:
            raise ValueError("coarse5 receipt is not bound to the authoritative run")
        if coarse.get("dispatch_allowed") is not False:
            raise ValueError("resume coarse5 path must remain non-dispatchable")
        if int(coarse.get("candidate_count", 0)) != 2:
            raise ValueError("coarse5 receipt does not contain both authoritative candidates")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "already_materialized",
            "decision": "prepared_not_dispatched",
            "materializer_called": False,
            "pg_write_count": 0,
            "coarse5_prepared_count": int(coarse["candidate_count"]),
            "nstruct": 5,
            "dispatch_allowed": False,
            "materialization_run_ids": sorted(str(item) for item in run_ids),
            "sequence_sha256s": list(manifest.sequence_sha256s),
            "materialization_receipt": str(materialization_path),
            "coarse5_receipt": str(
                destination / "coarse5_prepared" / "coarse5_prepared_receipt.json"
            ),
            "historical_runs_modified": False,
        }
    if existing:
        raise ValueError("partial existing materialization requires manual identity review")

    args = SimpleNamespace(
        candidate_scores=manifest.materialization_scores_path,
        challenger_review=manifest.materialization_challenger_path,
        source_receipt=manifest.close_path,
        source_commit=manifest.source_commit,
        batch_size=2,
    )
    materialized = dict(await materializer(args))
    run_id = str(materialized.get("operational_run_id") or "")
    if run_id != str(manifest.expected_run_id):
        raise ValueError("existing exact materializer returned an unexpected run identity")
    count = int(materialized.get("materialized_or_reused_in_run_count", 0))
    if count != 2:
        raise ValueError("recovery materializer did not materialize both QD contributors")
    if materialized.get("historical_runs_modified", False):
        raise ValueError("recovery modified a historical run")

    authoritative_candidates = dict(
        await _existing_candidates(manifest.sequence_sha256s, manifest.expected_run_id)
    )
    if len(authoritative_candidates) != 2:
        raise ValueError("materialized candidate readback is incomplete")

    destination = (output_dir or manifest.report_root / "resume_v2b").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    materialization_path = destination / "materialization_receipt.json"
    if materialization_path.exists():
        raise FileExistsError(materialization_path)
    materialization_path.write_text(
        json.dumps(materialized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    coarse = await _prepare_coarse5(
        coarse_builder,
        manifest=manifest,
        materialization_path=materialization_path,
        destination=destination,
        authoritative_candidates=authoritative_candidates,
    )
    if str(coarse.get("run_id")) != run_id or int(coarse.get("nstruct", 0)) != 5:
        raise ValueError("coarse5 receipt is not bound to the authoritative run")
    if coarse.get("dispatch_allowed") is not False:
        raise ValueError("resume coarse5 path must remain non-dispatchable")
    if int(coarse.get("candidate_count", 0)) != 2:
        raise ValueError("coarse5 receipt does not contain both authoritative candidates")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "materialized_and_coarse5_prepared",
        "decision": "prepared_not_dispatched",
        "materializer_called": True,
        "pg_write_count": int(materialized.get("inserted_evaluation_count", 0)) + 2,
        "operational_run_id": run_id,
        "materialized_candidate_count": count,
        "coarse5_prepared_count": int(coarse["candidate_count"]),
        "nstruct": 5,
        "dispatch_allowed": False,
        "materialization_receipt": str(materialization_path),
        "coarse5_receipt": str(destination / "coarse5_prepared" / "coarse5_prepared_receipt.json"),
        "historical_runs_modified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("resume receipt output is append-only")
    result = asyncio.run(resume(args.report_root, output_dir=args.output.parent))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
