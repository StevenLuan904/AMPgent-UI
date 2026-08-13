from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.v37_local_worker_windows import (
    LOCAL_V37_ROLES,
    PLAN_SCHEMA,
    RECEIPT_SCHEMA,
    LocalWorkerRefusal,
    _exclusive_reservation,
    _powershell_process_snapshot,
    _reject_mutable_pepagent_import_paths,
    inspect_local_worker_receipt,
    launch_local_worker,
    validate_local_worker_plan,
)


def test_runtime_rejects_mutable_pepagent_import_fallback(tmp_path: Path) -> None:
    release = tmp_path / "release"
    mutable_src = tmp_path / "workspace-src"
    (release / "src" / "pepagent").mkdir(parents=True)
    (mutable_src / "pepagent").mkdir(parents=True)
    with pytest.raises(LocalWorkerRefusal, match="mutable pepagent source"):
        _reject_mutable_pepagent_import_paths(
            import_paths=[str(mutable_src)], release_root=release
        )
    _reject_mutable_pepagent_import_paths(
        import_paths=[str(release / "src")], release_root=release
    )


def test_powershell_snapshot_forces_utf8_for_unicode_workspace_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '{"ProcessId":123,"ExecutablePath":'
                '"D:\\\\DWorkspace\\\\\u76ae\u80a4\u6297\u83cc\u77ed\u80bd\\\\python.exe",'
                '"CommandLine":"python -m worker","CreationDate":"created"}'
            ),
            stderr="",
        )

    monkeypatch.setattr("pepagent.v37_local_worker_windows.subprocess.run", fake_run)
    snapshot = _powershell_process_snapshot(123)
    assert snapshot["ProcessId"] == 123
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "strict"
    assert "[Console]::OutputEncoding" in captured["command"][-1]


def _plan(tmp_path: Path, *, role: str = "metrics") -> dict[str, object]:
    release = tmp_path / "release"
    module = LOCAL_V37_ROLES[role].worker_module
    entrypoint = release / "src" / Path(*module.split(".")).with_suffix(".py")
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("# frozen worker\n", encoding="utf-8")
    archive = tmp_path / "release.zip"
    archive.write_bytes(b"immutable release")
    release_sha = sha256_file(archive)
    source_revision = "1" * 40
    (release / ".pepagent-source-revision").write_text(source_revision, encoding="ascii")
    (release / ".pepagent-release-sha256").write_text(release_sha, encoding="ascii")
    python_path = tmp_path / "python.exe"
    python_path.write_bytes(b"managed python")
    identities: dict[str, dict[str, object]] = {}
    for name in sorted(LOCAL_V37_ROLES[role].required_identity_roles):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"name": name}), encoding="utf-8")
        identities[name] = {
            "path": str(path),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    plan: dict[str, object] = {
        "schema_version": PLAN_SCHEMA,
        "role": role,
        "instance": "local-1",
        "physical_host": "STEVENSOMEN9",
        "task_queue": LOCAL_V37_ROLES[role].task_queue,
        "poller_kinds": list(LOCAL_V37_ROLES[role].poller_kinds),
        "worker_module": module,
        "maximum_concurrent_activities": LOCAL_V37_ROLES[
            role
        ].maximum_concurrent_activities,
        "source_revision": source_revision,
        "worker_source_revision": source_revision,
        "implementation_revision": "2" * 40,
        "release_root": str(release),
        "release_archive_path": str(archive),
        "release_sha256": release_sha,
        "python_path": str(python_path),
        "python_sha256": sha256_file(python_path),
        "managed_python_executable": str(python_path),
        "managed_python_executable_sha256": sha256_file(python_path),
        "runtime_import_paths": [],
        "entrypoint_sha256": sha256_file(entrypoint),
        "runtime_environment_sha256": "3" * 64,
        "runtime_manifest": {},
        "pepagent_origin": str(release / "src" / "pepagent" / "__init__.py"),
        "identity_files": identities,
        "state_root": str(tmp_path / "state"),
        "temporal_address": "localhost:7233",
        "temporal_namespace": "default",
        "expected_temporal_identity": (
            f"pepagent:{role}:{{pid}}@STEVENSOMEN9:{source_revision}"
        ),
        "created_at": "2026-08-12T00:00:00+00:00",
    }
    plan["plan_sha256"] = sha256_json(plan)
    return plan


def test_role_contract_matches_frozen_v37_queues_and_modules() -> None:
    assert {
        role: (spec.task_queue, spec.worker_module, spec.maximum_concurrent_activities)
        for role, spec in LOCAL_V37_ROLES.items()
    } == {
        "v37-control": (
            "pepagent-control-v37",
            "pepagent.workers.v37_temporal_worker",
            16,
        ),
        "v37-generator": (
            "pepagent-generator-v37",
            "pepagent.workers.v37_temporal_worker",
            8,
        ),
        "v37-provider": (
            "pepagent-provider-v37",
            "pepagent.workers.v37_temporal_worker",
            1,
        ),
        "metrics": ("pepagent-cpu-metrics", "pepagent.workers.temporal_worker", 5),
    }


def test_plan_rehash_rejects_archive_python_entrypoint_and_runtime_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path)
    monkeypatch.setattr(
        "pepagent.v37_local_worker_windows._runtime_fingerprint",
        lambda **_: {
            "environment_sha256": "3" * 64,
            "manifest": {},
            "pepagent_origin": plan["pepagent_origin"],
            "import_paths": [],
        },
    )
    validate_local_worker_plan(plan, rehash_live_files=True)
    Path(plan["release_archive_path"]).write_bytes(b"drift")
    with pytest.raises(LocalWorkerRefusal, match="release archive changed"):
        validate_local_worker_plan(plan, rehash_live_files=True)


def test_plan_self_hash_and_queue_drift_fail_closed(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan["task_queue"] = "pepagent-control"
    with pytest.raises(LocalWorkerRefusal, match="self-hash"):
        validate_local_worker_plan(plan, rehash_live_files=False)
    plan["plan_sha256"] = sha256_json(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    with pytest.raises(LocalWorkerRefusal, match="role or task queue"):
        validate_local_worker_plan(plan, rehash_live_files=False)


def test_queue_reservation_is_atomic_and_refuses_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    _exclusive_reservation(path, {"status": "reserved"})
    with pytest.raises(LocalWorkerRefusal, match="ownership receipt already exists"):
        _exclusive_reservation(path, {"status": "second"})


def test_inspector_rejects_pid_reuse_python_and_command_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path)
    monkeypatch.setattr(
        "pepagent.v37_local_worker_windows._runtime_fingerprint",
        lambda **_: {
            "environment_sha256": "3" * 64,
            "manifest": {},
            "pepagent_origin": plan["pepagent_origin"],
            "import_paths": [],
        },
    )
    receipt: dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA,
        "status": "live_exact_worker_process",
        "pid": 123,
        "process_creation_date": "20260812120000.000000+480",
        "temporal_pollers": [],
        "log_path": "worker.log",
        "plan": plan,
        "launched_at": "2026-08-12T04:00:00+00:00",
    }
    receipt["receipt_sha256"] = sha256_json(receipt)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    valid_snapshot = {
        "ProcessId": 123,
        "ExecutablePath": plan["python_path"],
        "CommandLine": f'"{plan["python_path"]}" -m {plan["worker_module"]}',
        "CreationDate": receipt["process_creation_date"],
    }
    assert inspect_local_worker_receipt(
        receipt_path, process_snapshot=valid_snapshot
    )["status"] == "live_exact_worker_process"
    with pytest.raises(LocalWorkerRefusal, match="PID was reused"):
        inspect_local_worker_receipt(
            receipt_path,
            process_snapshot={**valid_snapshot, "CreationDate": "another"},
        )
    with pytest.raises(LocalWorkerRefusal, match="another Python"):
        inspect_local_worker_receipt(
            receipt_path,
            process_snapshot={**valid_snapshot, "ExecutablePath": str(tmp_path / "other.exe")},
        )
    with pytest.raises(LocalWorkerRefusal, match="not the planned worker module"):
        inspect_local_worker_receipt(
            receipt_path,
            process_snapshot={**valid_snapshot, "CommandLine": "python -m something.else"},
        )


def test_inspector_rejects_runtime_identity_file_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path)
    monkeypatch.setattr(
        "pepagent.v37_local_worker_windows._runtime_fingerprint",
        lambda **_: {
            "environment_sha256": "3" * 64,
            "manifest": {},
            "pepagent_origin": plan["pepagent_origin"],
            "import_paths": [],
        },
    )
    receipt: dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA,
        "status": "live_exact_worker_process",
        "pid": 123,
        "process_creation_date": "created",
        "temporal_pollers": [],
        "log_path": "worker.log",
        "plan": plan,
        "launched_at": "now",
    }
    receipt["receipt_sha256"] = sha256_json(receipt)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    metric_registry = Path(plan["identity_files"]["metric_registry"]["path"])
    metric_registry.write_text("drift", encoding="utf-8")
    with pytest.raises(LocalWorkerRefusal, match="runtime identity file changed"):
        inspect_local_worker_receipt(
            receipt_path,
            process_snapshot={
                "ProcessId": 123,
                "ExecutablePath": plan["python_path"],
                "CommandLine": f'python -m {plan["worker_module"]}',
                "CreationDate": "created",
            },
        )


@pytest.mark.asyncio
async def test_launch_refuses_existing_temporal_poller_before_spawn_or_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path)
    monkeypatch.setattr("pepagent.v37_local_worker_windows.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "pepagent.v37_local_worker_windows._runtime_fingerprint",
        lambda **_: {
            "environment_sha256": "3" * 64,
            "manifest": {},
            "pepagent_origin": plan["pepagent_origin"],
            "import_paths": [],
        },
    )

    async def existing_poller(_: dict[str, object]) -> list[dict[str, str]]:
        return [
            {
                "kind": "activity",
                "identity": "already-live",
                "last_access_time": "2026-08-12T00:00:00Z",
            }
        ]

    monkeypatch.setattr(
        "pepagent.v37_local_worker_windows.temporal_queue_pollers", existing_poller
    )

    def forbidden_spawn(*_: object, **__: object) -> None:
        raise AssertionError("duplicate gate must run before spawning")

    monkeypatch.setattr("pepagent.v37_local_worker_windows.subprocess.Popen", forbidden_spawn)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(LocalWorkerRefusal, match="already has live pollers"):
        await launch_local_worker(plan_path)
    assert not (tmp_path / "state").exists()


def test_local_poller_liveness_ignores_only_a_stopped_exact_local_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pepagent.v37_local_worker_windows import live_local_queue_pollers

    def snapshot(pid: int) -> dict[str, object]:
        if pid == 12:
            raise LocalWorkerRefusal("absent")
        return {"ProcessId": pid}

    monkeypatch.setattr(
        "pepagent.v37_local_worker_windows._powershell_process_snapshot", snapshot
    )
    pollers = [
        {
            "kind": "activity",
            "identity": f"pepagent:v37-control:{pid}@local-host:{'1' * 40}",
            "last_access_time": "2026-08-12T00:00:00Z",
        }
        for pid in (12, 13)
    ]
    pollers.append(
        {
            "kind": "activity",
            "identity": f"pepagent:v37-control:14@remote-host:{'1' * 40}",
            "last_access_time": "2026-08-12T00:00:00Z",
        }
    )
    assert [
        item["identity"]
        for item in live_local_queue_pollers(pollers, local_host="local-host")
    ] == [
        pollers[1]["identity"],
        pollers[2]["identity"],
    ]
