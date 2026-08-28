from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from pepagent.autoresearch_closed_loop import (
    ContinuationPolicy,
    MultiFrontArchivePolicy,
)
from pepagent.autoresearch_formal_submit_cli import (
    BRANCH_KEYS,
    CONFIG_SCHEMA,
    CONTROL_QUEUE,
    GENERATOR_QUEUE,
    METRICS_QUEUE,
    PEPMLM_REVISION,
    PEPMLM_WEIGHTS_SHA256,
    PREFLIGHT_SCHEMA,
    WORKFLOW_MEMO_KEY,
    WORKFLOW_TYPE,
    _start_or_recover_autoresearch_workflow,
    build_autoresearch_formal_plan,
    derive_autoresearch_branch_identity,
    main,
    reserve_autoresearch_formal_plan,
    submit_autoresearch_formal_plan,
)
from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text
from pepagent.storage.object_store import StoredObject
from pepagent.v38_science_execution import build_default_v38_sequence_contract


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _fixture_bundle(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source_revision = "a" * 40
    release_sha256 = "b" * 64
    control_environment = "c" * 64
    generator_environment = "d" * 64
    contract = build_default_v38_sequence_contract().model_dump(mode="json")
    plugins = {name: {"name": name} for name in contract["metric_plugins"]}
    sequences = {
        "acea": "MKTIIALSYIFCLVFAD",
        "gyra": "MARGKKIGYSAPRQTAA",
        "pbp2a": "MNNKDKDKKAIEDKNFQ",
        "vegfa": "MTDRQTDTAPSPSAHLL",
        "fgf2": "MAASGITSLPALPEDGG",
        "angpt1": "MTVFLSFAFFAAILTHI",
    }
    targets = []
    target_ids: dict[str, uuid.UUID] = {}
    for branch_key in BRANCH_KEYS:
        sequence = sequences[branch_key]
        sequence_sha256 = sha256_text(sequence)
        target_ids[branch_key] = uuid.uuid5(uuid.NAMESPACE_DNS, sequence_sha256)
        targets.append(
            {
                "target_key": branch_key,
                "sequence": sequence,
                "sequence_sha256": sequence_sha256,
            }
        )
    target_manifest = {
        "schema_version": "ampgent.target_sequence_manifest.v1",
        "target_count": 6,
        "targets": targets,
    }
    target_manifest_path = tmp_path / "targets.json"
    _write_json(target_manifest_path, target_manifest)
    receipt = {
        "schema_version": "v38.local-sequence-worker-receipt.1",
        "role": "autoresearch-control",
        "task_queue": CONTROL_QUEUE,
        "source_revision": source_revision,
        "release_sha256": release_sha256,
        "environment_sha256": control_environment,
        "task_queue_verified_from_release": True,
        "ampgent_owned": True,
        "foreign": False,
    }
    receipt_path = tmp_path / "autoresearch-control.json"
    _write_json(receipt_path, receipt)
    config: dict[str, Any] = {
        "schema_version": CONFIG_SCHEMA,
        "source_revision": source_revision,
        "release": {
            "source_revision": source_revision,
            "archive_sha256": release_sha256,
            "archive_path_or_uri": "s3://release/sha256/bb/archive.tar.gz",
        },
        "target_manifest": {
            "path": str(target_manifest_path),
            "sha256": sha256_file(target_manifest_path),
        },
        "runtime": {
            "control_environment_sha256": control_environment,
            "generator_environment_sha256": generator_environment,
            "metric_plugin_registry_sha256": sha256_json(plugins),
            "metric_plugins_by_name": plugins,
        },
        "temporal": {
            "control_queue": CONTROL_QUEUE,
            "generator_queue": GENERATOR_QUEUE,
            "metrics_queue": METRICS_QUEUE,
        },
        "model": {
            "pepmlm_revision": PEPMLM_REVISION,
            "pepmlm_weights_sha256": PEPMLM_WEIGHTS_SHA256,
        },
        "branches": [],
    }
    continuation = ContinuationPolicy(
        maximum_generations_per_run=30,
        minimum_high_quality_candidates=50,
        stagnation_patience_generations=3,
    ).model_dump(mode="json")
    archive = MultiFrontArchivePolicy().model_dump(mode="json")
    for ordinal, target in enumerate(targets, start=1):
        branch_key = target["target_key"]
        seed = {
            "bundle_key": f"bundle-{ordinal}",
            "bundle_receipt_path": f"bundle-{ordinal}.receipt.json",
            "bundle_receipt_sha256": f"{ordinal:x}" * 64,
            "source_map_path": f"source-map-{ordinal}.receipt.json",
            "source_map_sha256": f"{ordinal + 6:x}" * 64,
            "remote_cas_uri": (
                "ssh://example.invalid/cas/"
                f"{f'{ordinal + 6:x}' * 64}/source-map.json"
            ),
        }
        branch: dict[str, Any] = {
            "branch_key": branch_key,
            "target_id": str(target_ids[branch_key]),
            "target_sequence": target["sequence"],
            "target_sequence_sha256": target["sequence_sha256"],
            "request_path": str(tmp_path / f"{branch_key}.request.json"),
            "request_sha256": "",
            "formal_submission_key": "",
            "run_id": "",
            "workflow_id": "",
            "seed": seed,
            "continuation_policy": continuation,
        }
        request_template = {
            "schema_version": "ampgent.autoresearch-workflow-request.1",
            "branch_key": branch_key,
            "execution_contract": contract,
            "metric_plugins_by_name": plugins,
            "task_queues": {
                "workflow_and_control": CONTROL_QUEUE,
                "action_execution": GENERATOR_QUEUE,
                "sequence_metrics": METRICS_QUEUE,
            },
            "planner_provider": {
                "activity_name": "plan_autoresearch_actions",
                "task_queue": CONTROL_QUEUE,
                "planner_contract": {"de_novo_quota": 0.2},
            },
            "action_executor": {
                "operator_environment_sha256": generator_environment,
                "operator_release_sha256": release_sha256,
                "target_sequence": target["sequence"],
                "target_sequence_sha256": target["sequence_sha256"],
                "pepmlm_revision": PEPMLM_REVISION,
                "pepmlm_weights_sha256": PEPMLM_WEIGHTS_SHA256,
            },
            "archive_policy": archive,
            "continuation_policy": continuation,
            "control_environment_sha256": control_environment,
            "maximum_iterations_per_workflow_execution": 25,
            "seed_score_bundle_import": {
                "bundle_cache_root": r"C:\bounded-cache\score-all",
                "bundle_key": seed["bundle_key"],
                "bundle_receipt_path": seed["bundle_receipt_path"],
                "bundle_receipt_sha256": seed["bundle_receipt_sha256"],
                "source_map_receipt_path": seed["source_map_path"],
                "source_map_receipt_sha256": seed["source_map_sha256"],
                "source_map_storage_uri": seed["remote_cas_uri"],
                "target_key": branch_key,
            },
        }
        identity = derive_autoresearch_branch_identity(
            config=config,
            branch=branch,
            request_template=request_template,
        )
        for key in (
            "request_sha256",
            "formal_submission_key",
            "run_id",
            "workflow_id",
        ):
            branch[key] = identity[key]
        _write_json(Path(branch["request_path"]), identity["request"])
        config["branches"].append(branch)
    preflight = {
        "schema_version": PREFLIGHT_SCHEMA,
        "status": "ready",
        "checked_at": "2026-08-28T00:00:00Z",
        "source_revision": source_revision,
        "release_sha256": release_sha256,
        "config_sha256": sha256_json(config),
        "branch_count": 6,
        "branches": [
            {
                "branch_key": branch["branch_key"],
                "request_sha256": branch["request_sha256"],
                "formal_submission_key": branch["formal_submission_key"],
                "run_id": branch["run_id"],
                "workflow_id": branch["workflow_id"],
                "seed_receipt_sha256": branch["seed"][
                    "bundle_receipt_sha256"
                ],
                "source_map_sha256": branch["seed"]["source_map_sha256"],
                "status": "ready",
            }
            for branch in config["branches"]
        ],
        "checks": [
            {
                "name": "control_worker_receipt_identity",
                "status": "passed",
                "evidence": {
                    "mode": "autoresearch-local",
                    "receipt_path": str(receipt_path),
                    "receipt_sha256": sha256_file(receipt_path),
                    "receipt": receipt,
                },
            },
            {"name": "database_migration_0016", "status": "passed", "evidence": {}},
        ],
        "blockers": [],
    }
    return config, preflight


def _build_plan(tmp_path: Path):
    config, preflight = _fixture_bundle(tmp_path)
    return build_autoresearch_formal_plan(
        config=config,
        preflight=preflight,
        config_base_path=tmp_path,
        preflight_base_path=tmp_path,
    )


def test_formal_plan_derives_exact_six_new_branch_identities(tmp_path: Path) -> None:
    plan = _build_plan(tmp_path)

    assert tuple(item.branch_key for item in plan.branches) == BRANCH_KEYS
    assert len({item.run_id for item in plan.branches}) == 6
    assert len({item.workflow_id for item in plan.branches}) == 6
    assert len({item.formal_submission_key for item in plan.branches}) == 6
    assert all(item.request["run_id"] == str(item.run_id) for item in plan.branches)
    assert all(item.request_sha256 == sha256_json(item.request) for item in plan.branches)
    assert all(item.workflow_id.startswith("pepagent-autoresearch-v1-") for item in plan.branches)


def test_formal_plan_fails_closed_on_request_or_receipt_drift(tmp_path: Path) -> None:
    config, preflight = _fixture_bundle(tmp_path)
    request_path = Path(config["branches"][0]["request_path"])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["maximum_iterations_per_workflow_execution"] = 26
    _write_json(request_path, request)

    with pytest.raises(ValueError, match="request run identity is not deterministic"):
        build_autoresearch_formal_plan(
            config=config,
            preflight=preflight,
            config_base_path=tmp_path,
            preflight_base_path=tmp_path,
        )

    config, preflight = _fixture_bundle(tmp_path)
    preflight["checks"][0]["evidence"]["receipt"]["environment_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="embedded and on-disk"):
        build_autoresearch_formal_plan(
            config=config,
            preflight=preflight,
            config_base_path=tmp_path,
            preflight_base_path=tmp_path,
        )


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _FakeScalarRows:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeExecuteResult:
    def __init__(self, value: Any = None):
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _FakeSession:
    def __init__(self, scalar_batches: list[list[Any]], run_ids: list[uuid.UUID]):
        self.scalar_batches = list(scalar_batches)
        self.run_ids = list(run_ids)
        self.executed: list[Any] = []
        self.run_insert_count = 0
        self.artifact_insert_count = 0

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def execute(self, statement: Any, parameters: Any = None) -> _FakeExecuteResult:
        self.executed.append((statement, parameters))
        table = getattr(statement, "table", None)
        table_name = getattr(table, "name", None)
        if table_name == "experiment_runs":
            value = self.run_ids[self.run_insert_count]
            self.run_insert_count += 1
            return _FakeExecuteResult(value)
        if table_name == "artifacts":
            self.artifact_insert_count += 1
        return _FakeExecuteResult()

    async def scalars(self, _statement: Any) -> _FakeScalarRows:
        return _FakeScalarRows(self.scalar_batches.pop(0))


class _FakeSessionContext:
    def __init__(self, session: _FakeSession):
        self.session = session

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _FakeObjectStore:
    def put_bytes(self, payload: bytes, media_type: str) -> StoredObject:
        digest = sha256_text(payload.decode("utf-8"))
        return StoredObject(
            sha256=digest,
            size_bytes=len(payload),
            uri=f"s3://test/sha256/{digest[:2]}/{digest}",
            media_type=media_type,
        )


class _FakeRepository:
    events: list[tuple[Any, ...]] = []

    def __init__(self, _session: Any):
        pass

    async def append_event(self, *args: Any) -> None:
        self.events.append(args)


@pytest.mark.asyncio
async def test_reservation_uses_one_advisory_transaction_and_six_unique_runs(
    tmp_path: Path,
) -> None:
    plan = _build_plan(tmp_path)
    targets = [
        SimpleNamespace(
            id=item.target_id,
            sequence=item.target_sequence,
            sequence_sha256=item.target_sequence_sha256,
        )
        for item in plan.branches
    ]
    session = _FakeSession(
        scalar_batches=[[], targets], run_ids=[item.run_id for item in plan.branches]
    )
    _FakeRepository.events = []

    reservation = await reserve_autoresearch_formal_plan(
        plan,
        session_factory=lambda: _FakeSessionContext(session),
        object_store_factory=_FakeObjectStore,
        repository_factory=_FakeRepository,
    )

    assert reservation.created is True
    assert session.run_insert_count == 6
    assert session.artifact_insert_count == 6
    assert len(_FakeRepository.events) == 12
    assert "pg_advisory_xact_lock" in str(session.executed[0][0])
    assert all(spec["parent_run_id"] is None for spec in reservation.branch_specs.values())
    assert all(
        spec["historical_outputs_reused"] is False
        for spec in reservation.branch_specs.values()
    )

    existing = []
    for branch in plan.branches:
        spec = reservation.branch_specs[branch.branch_key]
        existing.append(
            SimpleNamespace(
                id=branch.run_id,
                target_id=branch.target_id,
                spec_json=copy.deepcopy(spec),
                spec_sha256=sha256_json(spec),
                formal_submission_key=branch.formal_submission_key,
                status="created",
                temporal_workflow_id=branch.workflow_id,
                temporal_run_id=None,
                parent_run_id=None,
            )
        )
    recovery_session = _FakeSession(
        scalar_batches=[existing], run_ids=[item.run_id for item in plan.branches]
    )
    _FakeRepository.events = []
    recovered = await reserve_autoresearch_formal_plan(
        plan,
        session_factory=lambda: _FakeSessionContext(recovery_session),
        object_store_factory=_FakeObjectStore,
        repository_factory=_FakeRepository,
    )
    assert recovered.created is False
    assert recovery_session.run_insert_count == 0
    assert _FakeRepository.events == []


class _FakeHandle:
    def __init__(self, *, run_id: str, memo: dict[str, Any] | None = None):
        self.run_id = run_id
        self.memo = memo

    async def describe(self) -> Any:
        return SimpleNamespace(
            workflow_type=WORKFLOW_TYPE,
            memo=self.memo,
            run_id=self.run_id,
        )


class _FakeClient:
    def __init__(self, *, already_started: bool = False, memo: Any = None):
        self.already_started = already_started
        self.memo = memo
        self.calls: list[dict[str, Any]] = []
        self.handles: dict[str, _FakeHandle] = {}

    async def start_workflow(
        self, workflow: str, request: dict[str, Any], **kwargs: Any
    ) -> _FakeHandle:
        self.calls.append({"workflow": workflow, "request": request, **kwargs})
        workflow_id = kwargs["id"]
        if self.already_started:
            raise WorkflowAlreadyStartedError(workflow_id, workflow)
        handle = _FakeHandle(run_id=f"temporal-{workflow_id}", memo=kwargs["memo"])
        self.handles[workflow_id] = handle
        return handle

    def get_workflow_handle(self, workflow_id: str) -> _FakeHandle:
        return self.handles[workflow_id]


@pytest.mark.asyncio
async def test_temporal_submit_uses_reject_duplicate_and_exact_memo_recovery(
    tmp_path: Path,
) -> None:
    plan = _build_plan(tmp_path)
    branch = plan.branches[0]
    client = _FakeClient()

    binding = await _start_or_recover_autoresearch_workflow(
        client, plan=plan, branch=branch
    )
    call = client.calls[0]
    assert binding.recovered is False
    assert call["workflow"] == WORKFLOW_TYPE
    assert call["task_queue"] == CONTROL_QUEUE
    assert call["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
    assert call["id_conflict_policy"] == WorkflowIDConflictPolicy.FAIL
    assert call["memo"][WORKFLOW_MEMO_KEY]["request_sha256"] == branch.request_sha256

    recovery_client = _FakeClient(already_started=True)
    recovery_client.handles[branch.workflow_id] = _FakeHandle(
        run_id="recovered-run", memo=call["memo"]
    )
    recovered = await _start_or_recover_autoresearch_workflow(
        recovery_client, plan=plan, branch=branch
    )
    assert recovered.recovered is True
    assert recovered.temporal_run_id == "recovered-run"

    drift_client = _FakeClient(already_started=True)
    drift_client.handles[branch.workflow_id] = _FakeHandle(
        run_id="wrong-run", memo={WORKFLOW_MEMO_KEY: {"request_sha256": "0" * 64}}
    )
    with pytest.raises(ValueError, match="memo identity drifted"):
        await _start_or_recover_autoresearch_workflow(
            drift_client, plan=plan, branch=branch
        )


@pytest.mark.asyncio
async def test_submit_binds_all_six_temporal_runs_and_appends_lifecycle(
    tmp_path: Path,
) -> None:
    plan = _build_plan(tmp_path)
    targets = [
        SimpleNamespace(
            id=item.target_id,
            sequence=item.target_sequence,
            sequence_sha256=item.target_sequence_sha256,
        )
        for item in plan.branches
    ]
    reserve_session = _FakeSession(
        scalar_batches=[[], targets], run_ids=[item.run_id for item in plan.branches]
    )
    _FakeRepository.events = []
    reservation = await reserve_autoresearch_formal_plan(
        plan,
        session_factory=lambda: _FakeSessionContext(reserve_session),
        object_store_factory=_FakeObjectStore,
        repository_factory=_FakeRepository,
    )
    runs = []
    for branch in plan.branches:
        spec = reservation.branch_specs[branch.branch_key]
        runs.append(
            SimpleNamespace(
                id=branch.run_id,
                target_id=branch.target_id,
                spec_json=copy.deepcopy(spec),
                spec_sha256=sha256_json(spec),
                formal_submission_key=branch.formal_submission_key,
                status="created",
                temporal_workflow_id=branch.workflow_id,
                temporal_run_id=None,
                parent_run_id=None,
            )
        )
    submit_session = _FakeSession(
        scalar_batches=[runs], run_ids=[item.run_id for item in plan.branches]
    )
    _FakeRepository.events = []
    result = await submit_autoresearch_formal_plan(
        plan,
        reservation,
        client=_FakeClient(),
        session_factory=lambda: _FakeSessionContext(submit_session),
        repository_factory=_FakeRepository,
    )

    assert result["branch_count"] == 6
    assert all(item.status == "running" for item in runs)
    assert all(item.temporal_run_id for item in runs)
    assert len(_FakeRepository.events) == 6


def test_cli_is_inert_without_explicit_execute(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, preflight = _fixture_bundle(tmp_path)
    config_path = tmp_path / "submission.json"
    preflight_path = tmp_path / "preflight.json"
    _write_json(config_path, config)
    _write_json(preflight_path, preflight)

    assert main(["--config", str(config_path), "--preflight", str(preflight_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["executed"] is False
    assert payload["inert"] is True

    with pytest.raises(SystemExit, match="requires explicit --execute"):
        main(
            [
                "--config",
                str(config_path),
                "--preflight",
                str(preflight_path),
                "--reserve-only",
            ]
        )
