from __future__ import annotations

import argparse
import asyncio
import io
import json
import stat
import tempfile
import uuid
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from sqlalchemy import func, select

from pepagent.db.models import (
    AgentDecision,
    AgentDecisionToolCallEdge,
    Artifact,
    Candidate,
    Evaluation,
    EvidenceArtifact,
    ExperimentRun,
    LifecycleEvent,
    ToolCall,
    ToolCallDependency,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.enums import RunStatus
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.storage.object_store import ContentAddressedObjectStore, StoredObject
from pepagent.v34_provider_releases import (
    verify_knowledge_provider_release,
    verify_pepshot_provider_release,
)
from pepagent.workers.activities import _register_artifact

SHADOW_VERSION = "v34.provider-shadow.0.1"
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_MAX_ARCHIVE_FILES = 256
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _safe_member_name(name: str) -> PurePosixPath:
    candidate = PurePosixPath(name.replace("\\", "/"))
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ValueError("v34 provider archive contains an unsafe member path")
    if candidate.parts[0].endswith(":"):
        raise ValueError("v34 provider archive contains a drive-qualified path")
    return candidate


def _selected_release_directory(provider_root: Path) -> Path:
    latest = json.loads((provider_root / "LATEST.json").read_text(encoding="utf-8"))
    if "relative_path" in latest:
        relative = _safe_member_name(str(latest["relative_path"]))
        release_dir = provider_root.joinpath(*relative.parts)
    else:
        manifest = _safe_member_name(str(latest.get("release_manifest", "")))
        release_dir = provider_root.joinpath(*manifest.parent.parts)
    resolved_root = provider_root.resolve()
    resolved_release = release_dir.resolve()
    if resolved_release == resolved_root or resolved_root not in resolved_release.parents:
        raise ValueError("v34 provider release escapes its release root")
    if not resolved_release.is_dir():
        raise FileNotFoundError("v34 provider release directory is missing")
    return resolved_release


def build_provider_release_archive(provider_root: Path) -> bytes:
    """Build a deterministic, path-independent snapshot of LATEST plus its selected release."""
    provider_root = provider_root.resolve()
    latest_path = provider_root / "LATEST.json"
    if not latest_path.is_file():
        raise FileNotFoundError("v34 provider LATEST pointer is missing")
    release_dir = _selected_release_directory(provider_root)
    files = [latest_path, *(path for path in release_dir.rglob("*") if path.is_file())]
    if len(files) > _MAX_ARCHIVE_FILES:
        raise ValueError("v34 provider release contains too many files")
    total_bytes = sum(path.stat().st_size for path in files)
    if total_bytes > _MAX_ARCHIVE_BYTES:
        raise ValueError("v34 provider release is too large for the shadow contract")
    seen: set[str] = set()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(files, key=lambda item: item.relative_to(provider_root).as_posix()):
            if path.is_symlink():
                raise ValueError("v34 provider release cannot contain symlinks")
            name = path.relative_to(provider_root).as_posix()
            _safe_member_name(name)
            if name in seen:
                raise ValueError("v34 provider release contains duplicate paths")
            seen.add(name)
            info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def _extract_provider_archive(archive_bytes: bytes, destination: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
        infos = archive.infolist()
        if not infos or len(infos) > _MAX_ARCHIVE_FILES:
            raise ValueError("v34 provider archive file count is invalid")
        names: set[str] = set()
        total_bytes = 0
        for info in infos:
            member = _safe_member_name(info.filename)
            if info.filename in names or info.is_dir():
                raise ValueError("v34 provider archive members are duplicated or non-files")
            names.add(info.filename)
            unix_mode = info.external_attr >> 16
            if stat.S_ISLNK(unix_mode):
                raise ValueError("v34 provider archive cannot contain symlinks")
            total_bytes += info.file_size
            if total_bytes > _MAX_ARCHIVE_BYTES:
                raise ValueError("v34 provider archive expands beyond its size limit")
            target = destination.joinpath(*member.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
    if "LATEST.json" not in names:
        raise ValueError("v34 provider archive omits LATEST.json")


def verify_provider_archive(
    provider: str,
    archive_bytes: bytes,
    provider_contract: dict[str, Any],
) -> dict[str, Any]:
    """Reverify a provider release using only portable archive bytes."""
    with tempfile.TemporaryDirectory(prefix="ampgent-v34-provider-") as temp:
        root = Path(temp)
        _extract_provider_archive(archive_bytes, root)
        if provider == "knowledge":
            receipt = verify_knowledge_provider_release(
                root / "LATEST.json",
                expected_latest_sha256=provider_contract["latest_sha256"],
                expected_revision=provider_contract["release_revision"],
                expected_release_manifest_sha256=provider_contract[
                    "release_manifest_sha256"
                ],
                expected_runtime_manifest_sha256=provider_contract[
                    "runtime_manifest_sha256"
                ],
                expected_policy_snapshot_sha256=provider_contract[
                    "active_policy_sha256"
                ],
                expected_policy_selection_receipt_sha256=provider_contract[
                    "policy_selection_receipt_sha256"
                ],
                expected_policy_roles_sha256=provider_contract["policy_roles_sha256"],
                expected_policy_record_content_sha256=provider_contract[
                    "policy_record_content_sha256"
                ],
                expected_policy_specification_sha256=provider_contract[
                    "policy_specification_sha256"
                ],
            )
        elif provider == "pepshot":
            receipt = verify_pepshot_provider_release(
                root / "LATEST.json",
                expected_latest_sha256=provider_contract["latest_sha256"],
                expected_source_revision=provider_contract["normalized_source_revision"],
                expected_release_id=provider_contract["release_id"],
                expected_release_manifest_sha256=provider_contract[
                    "release_manifest_sha256"
                ],
                expected_runtime_manifest_sha256=provider_contract[
                    "runtime_manifest_sha256"
                ],
                expected_bundle_id=provider_contract["fixed_fixture_bundle_id"],
            )
        else:
            raise ValueError(f"unsupported v34 provider: {provider}")
    return {
        **receipt,
        "release_archive_sha256": sha256_bytes(archive_bytes),
        "verified_from_portable_archive": True,
    }


def build_shadow_replay_bundle(
    *,
    contract_bytes: bytes,
    release_archives: dict[str, bytes],
    receipts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    expected = {provider: sha256_bytes(payload) for provider, payload in release_archives.items()}
    if set(expected) != {"knowledge", "pepshot"} or set(receipts) != set(expected):
        raise ValueError("v34 shadow requires exactly knowledge and PepShot evidence")
    for provider, receipt in receipts.items():
        if (
            receipt.get("provider_contract_verified") is not True
            or receipt.get("verified_from_portable_archive") is not True
            or receipt.get("release_archive_sha256") != expected[provider]
        ):
            raise ValueError(f"v34 {provider} portable-release receipt is incomplete")
    replay = {
        "schema_version": "1.0",
        "mode": "database_and_object_store_only_provider_shadow",
        "contract_sha256": sha256_bytes(contract_bytes),
        "release_archive_sha256": expected,
        "provider_receipt_sha256": {
            provider: sha256_json(receipt) for provider, receipt in receipts.items()
        },
        "candidate_count": 0,
        "evaluation_count": 0,
        "new_sequence_generation": False,
        "provider_effectiveness_evaluated": False,
        "formal_v34_authorized": False,
        "exact_replay": True,
        "verdict": "provider_releases_replayable_for_v34_authorization_request",
    }
    replay["replay_sha256"] = sha256_json(replay)
    return replay


def _validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("benchmark_id") != "amp_v34_provider_shadow":
        raise ValueError("unexpected v34 provider shadow benchmark")
    if contract.get("execution_authorized") is not True:
        raise ValueError("v34 provider shadow execution is not authorized")
    if contract.get("formal_v34_execution_authorized") is not False:
        raise ValueError("provider shadow cannot authorize v34 formal execution")
    if contract.get("submitted") is not False or contract.get("run_id") is not None:
        raise ValueError("v34 provider shadow has already been submitted")
    boundaries = contract.get("scientific_contract", {})
    required = (
        "no_candidate_generation",
        "no_evaluations",
        "provider_effectiveness_not_evaluated",
        "database_and_object_store_only_replay",
        "provider_owned_repairs_only",
        "formal_v34_requires_separate_authorization",
    )
    if not all(boundaries.get(key) is True for key in required):
        raise ValueError("v34 provider shadow scientific boundaries are incomplete")
    for provider in ("knowledge", "pepshot"):
        provider_contract = contract.get("providers", {}).get(provider, {})
        for field in ("release_archive_sha256", "consumer_receipt_sha256"):
            value = str(provider_contract.get(field, ""))
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"v34 {provider} {field} is not frozen")


async def _lineage_parent(contract: dict[str, Any]) -> ExperimentRun:
    parent_id = uuid.UUID(contract["lineage_parent_run_id"])
    async with SessionFactory() as session:
        parent = await session.get(ExperimentRun, parent_id)
        if parent is None or parent.status != RunStatus.SUCCEEDED:
            raise ValueError("v34 provider shadow lineage parent is not succeeded")
        await session.refresh(parent)
        return parent


async def _create_shadow_run(
    contract: dict[str, Any], parent: ExperimentRun
) -> ExperimentRun:
    async with SessionFactory() as session, session.begin():
        existing = list(
            await session.scalars(
                select(ExperimentRun).where(ExperimentRun.parent_run_id == parent.id)
            )
        )
        if any(item.spec_json.get("benchmark_id") == contract["benchmark_id"] for item in existing):
            raise ValueError("v34 provider shadow has already been submitted")
        run = ExperimentRun(
            target_id=parent.target_id,
            spec_json=contract,
            spec_sha256=sha256_json(contract),
            status=RunStatus.RUNNING,
            parent_run_id=parent.id,
            temporal_workflow_id=f"database-native-{contract['benchmark_id']}",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        repository = ExperimentRepository(session)
        await repository.append_event(
            "run", run.id, "run.created", "v34-provider-shadow", {"parent_run_id": str(parent.id)}
        )
        await repository.append_event(
            "run",
            run.id,
            "run.started",
            "v34-provider-shadow",
            {"mode": "database_native_provider_release_shadow"},
        )
        return run


async def _artifact_payload(run_id: uuid.UUID, role: str) -> bytes:
    async with SessionFactory() as session:
        result = await session.execute(
            select(Artifact)
            .join(EvidenceArtifact, EvidenceArtifact.artifact_id == Artifact.id)
            .join(ToolCall, ToolCall.id == EvidenceArtifact.tool_call_id)
            .where(ToolCall.run_id == run_id, EvidenceArtifact.role == role)
        )
        artifact = result.scalar_one()
    payload = await asyncio.to_thread(
        ContentAddressedObjectStore().get_bytes, artifact.storage_uri
    )
    if sha256_bytes(payload) != artifact.sha256:
        raise OSError(f"v34 shadow artifact failed SHA verification: {role}")
    return payload


async def _shadow_counts(run_id: uuid.UUID) -> dict[str, int]:
    async with SessionFactory() as session:
        tool_ids = select(ToolCall.id).where(ToolCall.run_id == run_id)
        candidate_ids = select(Candidate.id).where(Candidate.run_id == run_id)
        return {
            "candidate_count": int(
                await session.scalar(
                    select(func.count()).select_from(Candidate).where(Candidate.run_id == run_id)
                )
            ),
            "evaluation_count": int(
                await session.scalar(
                    select(func.count())
                    .select_from(Evaluation)
                    .where(Evaluation.candidate_id.in_(candidate_ids))
                )
            ),
            "tool_call_count": int(
                await session.scalar(
                    select(func.count()).select_from(ToolCall).where(ToolCall.run_id == run_id)
                )
            ),
            "dependency_count": int(
                await session.scalar(
                    select(func.count())
                    .select_from(ToolCallDependency)
                    .where(ToolCallDependency.child_tool_call_id.in_(tool_ids))
                )
            ),
            "decision_count": int(
                await session.scalar(
                    select(func.count())
                    .select_from(AgentDecision)
                    .where(AgentDecision.run_id == run_id)
                )
            ),
            "decision_edge_count": int(
                await session.scalar(
                    select(func.count())
                    .select_from(AgentDecisionToolCallEdge)
                    .where(AgentDecisionToolCallEdge.decision_id.in_(
                        select(AgentDecision.id).where(AgentDecision.run_id == run_id)
                    ))
                )
            ),
            "artifact_count": int(
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceArtifact)
                    .where(EvidenceArtifact.tool_call_id.in_(tool_ids))
                )
            ),
            "lifecycle_event_count": int(
                await session.scalar(
                    select(func.count())
                    .select_from(LifecycleEvent)
                    .where(
                        LifecycleEvent.aggregate_type == "run",
                        LifecycleEvent.aggregate_id == run_id,
                    )
                )
            ),
        }


async def execute_provider_shadow(
    config_path: Path,
    *,
    knowledge_release_root: Path,
    pepshot_release_root: Path,
) -> dict[str, Any]:
    contract_bytes = await asyncio.to_thread(config_path.read_bytes)
    contract = yaml.safe_load(contract_bytes)
    _validate_contract(contract)
    parent = await _lineage_parent(contract)

    roots = {
        "knowledge": knowledge_release_root,
        "pepshot": pepshot_release_root,
    }
    archives = {
        provider: await asyncio.to_thread(build_provider_release_archive, root)
        for provider, root in roots.items()
    }
    for provider, payload in archives.items():
        expected_archive_sha = contract["providers"][provider]["release_archive_sha256"]
        if sha256_bytes(payload) != expected_archive_sha:
            raise ValueError(f"v34 {provider} deterministic release archive drifted")
    initial_receipts = {
        provider: verify_provider_archive(
            provider, archives[provider], contract["providers"][provider]
        )
        for provider in ("knowledge", "pepshot")
    }
    for provider, receipt in initial_receipts.items():
        if receipt["receipt_sha256"] != contract["providers"][provider][
            "consumer_receipt_sha256"
        ]:
            raise ValueError(f"v34 {provider} consumer receipt drifted")
    contract_object = await asyncio.to_thread(
        ContentAddressedObjectStore().put_bytes, contract_bytes, "application/yaml"
    )
    archive_objects = {
        provider: await asyncio.to_thread(
            ContentAddressedObjectStore().put_bytes,
            payload,
            "application/zip",
        )
        for provider, payload in archives.items()
    }

    run = await _create_shadow_run(contract, parent)
    try:
        async with SessionFactory() as session, session.begin():
            repository = ExperimentRepository(session)
            sealer = await repository.record_completed_tool_call(
                run.id,
                "v34-provider-release-input-sealer",
                SHADOW_VERSION,
                sha256_json({"implementation_revision": contract["implementation_revision"]}),
                {
                    "contract_sha256": sha256_bytes(contract_bytes),
                    "provider_release_archive_sha256": {
                        provider: item.sha256 for provider, item in archive_objects.items()
                    },
                },
                {"local_provider_paths_persisted": False},
                {"sealed": True},
                model_uri="deterministic://v34-provider-release-input-sealer",
            )
            await _register_artifact(
                session,
                sealer.id,
                asdict(contract_object),
                "shadow_contract",
                {"benchmark_id": contract["benchmark_id"]},
            )
            for provider, item in archive_objects.items():
                await _register_artifact(
                    session,
                    sealer.id,
                    asdict(item),
                    f"{provider}_provider_release_archive",
                    {"provider": provider, "portable": True},
                )

        replay_contract_bytes = await _artifact_payload(run.id, "shadow_contract")
        replay_archives = {
            provider: await _artifact_payload(
                run.id, f"{provider}_provider_release_archive"
            )
            for provider in ("knowledge", "pepshot")
        }
        replay_receipts = {
            provider: verify_provider_archive(
                provider, replay_archives[provider], contract["providers"][provider]
            )
            for provider in ("knowledge", "pepshot")
        }
        if initial_receipts != replay_receipts or replay_contract_bytes != contract_bytes:
            raise ValueError("v34 provider shadow changed during database/object replay")

        receipt_objects: dict[str, StoredObject] = {}
        store = ContentAddressedObjectStore()
        for provider, receipt in replay_receipts.items():
            receipt_objects[provider] = await asyncio.to_thread(
                store.put_bytes, canonical_json_bytes(receipt), "application/json"
            )
        replay = build_shadow_replay_bundle(
            contract_bytes=replay_contract_bytes,
            release_archives=replay_archives,
            receipts=replay_receipts,
        )
        replay_object = await asyncio.to_thread(
            store.put_bytes, canonical_json_bytes(replay), "application/json"
        )

        async with SessionFactory() as session, session.begin():
            repository = ExperimentRepository(session)
            sealer_id = await session.scalar(
                select(ToolCall.id).where(
                    ToolCall.run_id == run.id,
                    ToolCall.tool_name == "v34-provider-release-input-sealer",
                )
            )
            verifier_ids: dict[str, uuid.UUID] = {}
            for provider in ("knowledge", "pepshot"):
                receipt = replay_receipts[provider]
                call = await repository.record_completed_tool_call(
                    run.id,
                    f"v34-{provider}-provider-release-verifier",
                    SHADOW_VERSION,
                    sha256_json(contract["providers"][provider]),
                    {
                        "release_archive_sha256": sha256_bytes(replay_archives[provider]),
                        "input_sealer_tool_call_id": str(sealer_id),
                    },
                    {"database_and_object_store_only": True},
                    receipt,
                    model_uri=f"deterministic://v34-{provider}-provider-release-verifier",
                )
                verifier_ids[provider] = call.id
                await repository.record_tool_dependency(
                    call.id, sealer_id, "verifies_sealed_provider_release"
                )
                await _register_artifact(
                    session,
                    call.id,
                    asdict(receipt_objects[provider]),
                    f"{provider}_provider_release_receipt",
                    {"provider": provider, "verified_from_portable_archive": True},
                )
            replay_call = await repository.record_completed_tool_call(
                run.id,
                "v34-provider-shadow-database-replay-verifier",
                SHADOW_VERSION,
                sha256_json({"implementation": "provider-shadow-replay-v1"}),
                {
                    "input_sealer_tool_call_id": str(sealer_id),
                    "provider_verifier_tool_call_ids": {
                        key: str(value) for key, value in verifier_ids.items()
                    },
                },
                {"external_provider_directories_read": False},
                replay,
                model_uri="deterministic://v34-provider-shadow-database-replay-verifier",
            )
            await repository.record_tool_dependency(
                replay_call.id, sealer_id, "replays_sealed_shadow_contract_and_releases"
            )
            for provider, verifier_id in verifier_ids.items():
                await repository.record_tool_dependency(
                    replay_call.id, verifier_id, f"replays_{provider}_verification"
                )
            replay_artifact = await _register_artifact(
                session,
                replay_call.id,
                asdict(replay_object),
                "provider_shadow_replay_bundle",
                {"exact_replay": True, "formal_v34_authorized": False},
            )
            decision_payload = {
                "verdict": replay["verdict"],
                "provider_contracts_verified": True,
                "database_and_object_store_only_replay": True,
                "candidate_count": 0,
                "evaluation_count": 0,
                "tool_effectiveness_claimed": False,
                "formal_v34_authorized": False,
            }
            response = json.dumps(
                decision_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            decision = await repository.record_agent_decision(
                run.id,
                0,
                "v34_provider_release_shadow_acceptance",
                "deterministic-provider-shadow-agent",
                SHADOW_VERSION,
                "Decide only whether both provider releases are portable and replayable from the "
                "AMPgent evidence graph; do not evaluate tool effectiveness or authorize v34.",
                response,
                decision_payload,
                response_artifact_id=replay_artifact.id,
            )
            for provider, verifier_id in verifier_ids.items():
                await repository.record_agent_tool_edge(
                    decision.id, verifier_id, "input", f"observes_{provider}_release_verification"
                )
            await repository.record_agent_tool_edge(
                decision.id, replay_call.id, "output", "materializes_provider_shadow_verdict"
            )
            stored_run = await session.get(ExperimentRun, run.id, with_for_update=True)
            stored_run.status = RunStatus.SUCCEEDED
            stored_run.finished_at = datetime.now(UTC)
            await repository.append_event(
                "run",
                run.id,
                "v34.provider_shadow_completed",
                "deterministic-provider-shadow-agent",
                {
                    "verdict": replay["verdict"],
                    "replay_artifact_sha256": replay_object.sha256,
                    "formal_v34_authorized": False,
                },
            )
    except Exception:
        async with SessionFactory() as session, session.begin():
            repository = ExperimentRepository(session)
            stored_run = await session.get(ExperimentRun, run.id, with_for_update=True)
            stored_run.status = RunStatus.FAILED
            stored_run.finished_at = datetime.now(UTC)
            await repository.append_event(
                "run", run.id, "v34.provider_shadow_failed", "v34-provider-shadow", {}
            )
        raise

    counts = await _shadow_counts(run.id)
    expected_counts = {
        "candidate_count": 0,
        "evaluation_count": 0,
        "tool_call_count": 4,
        "dependency_count": 5,
        "decision_count": 1,
        "decision_edge_count": 3,
        "artifact_count": 6,
        "lifecycle_event_count": 8,
    }
    if counts != expected_counts:
        async with SessionFactory() as session, session.begin():
            repository = ExperimentRepository(session)
            stored_run = await session.get(ExperimentRun, run.id, with_for_update=True)
            stored_run.status = RunStatus.FAILED
            await repository.append_event(
                "run",
                run.id,
                "v34.provider_shadow_count_audit_failed",
                "v34-provider-shadow",
                {"observed": counts, "expected": expected_counts},
            )
        raise ValueError(f"v34 provider shadow graph counts drifted: {counts}")
    return {
        "run_id": str(run.id),
        "lineage_parent_run_id": str(parent.id),
        "status": "succeeded",
        "verdict": replay["verdict"],
        "counts": counts,
        "artifacts": {
            "shadow_contract": asdict(contract_object),
            **{
                f"{provider}_provider_release_archive": asdict(item)
                for provider, item in archive_objects.items()
            },
            **{
                f"{provider}_provider_release_receipt": asdict(item)
                for provider, item in receipt_objects.items()
            },
            "provider_shadow_replay_bundle": asdict(replay_object),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--knowledge-release-root", type=Path, required=True)
    parser.add_argument("--pepshot-release-root", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        execute_provider_shadow(
            args.config,
            knowledge_release_root=args.knowledge_release_root,
            pepshot_release_root=args.pepshot_release_root,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
