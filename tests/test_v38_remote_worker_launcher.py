from pathlib import Path

SCRIPT = Path("deploy/remote/start_v38_worker.sh")


def test_v38_remote_launcher_is_role_and_placement_scoped() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "v38-boltz|v38-rosetta|autoresearch-generator" in text
    assert "192.168.99.32:/data1/luanhaoyang/pepagent:v38-boltz:1" in text
    assert (
        "192.168.99.32:/data1/luanhaoyang/pepagent:"
        "autoresearch-generator:1"
    ) in text
    assert "synth:/sdd_data/pepagent:v38-rosetta:cpu" in text
    assert "pepagent-autoresearch-generator-v1" in text
    assert "192.168.99.32:/data1/luanhaoyang/pepagent:autoresearch-generator:0" not in text
    assert "v38-boltz:2" not in text
    assert "v38-boltz:3" not in text
    assert "GPU2" not in text
    assert "GPU3" not in text


def test_v38_remote_launcher_verifies_immutable_runtime_before_launch() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    launch = text.index("nohup env")
    required_before_launch = (
        ".pepagent-source-revision",
        "managed Boltz executable is missing",
        "2.2.1",
        "v38 service tunnel preflight failed",
        "ENVIRONMENT_SHA256",
        "WEIGHTS_SHA256",
        "attest_v38_boltz_runtime.sh",
        "PEPAGENT_BOLTZ_GUARDED_SMOKE_SHA256",
        "GPU has compute processes; refusing launch",
        "GPU has a CUDA_VISIBLE_DEVICES declaration; refusing launch",
        "GPU exceeds the guarded idle threshold; refusing launch",
        "managed AutoResearch PepMLM runtime is missing",
        "AutoResearch PepMLM weights drifted",
        "AutoResearch generator CUDA placement preflight failed",
        "pepagent-autoresearch-generator-v1",
        "worker release task queue differs from the launcher contract",
    )
    for marker in required_before_launch:
        assert text.index(marker) < launch
    assert "pepagent.workers.v38_temporal_worker" in text
    assert "pepagent.workers.temporal_worker" not in text
    assert 'bash "$RELEASE_DIR/deploy/remote/attest_v38_boltz_runtime.sh"' in text
    for line in text.splitlines():
        if "nvidia-smi" in line:
            assert '-i "$RESOURCE"' in line


def test_autoresearch_generator_receipt_closes_placement_and_runtime_identity() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    launch = text.index("nohup env")
    for marker in (
        '"PEPAGENT_PEPMLM_MODEL_PATH=$MODEL_PATH"',
        '"PEPAGENT_PEPMLM_MODEL_REVISION=$MODEL_REVISION"',
        '"PEPAGENT_PEPMLM_WEIGHTS_SHA256=$WEIGHTS_SHA256"',
    ):
        assert marker in text
    assert '"${EXTRA_ENV[@]}"' in text[launch:]
    for marker in (
        'PEPAGENT_WORKER_ROLE="$ROLE"',
        'PEPAGENT_WORKER_GPU_INDEX="$RESOURCE"',
        'PEPAGENT_WORK_ROOT="$WORK_ROOT"',
    ):
        assert marker in text[launch:]
    for receipt_field in (
        'RECEIPT_SCHEMA="autoresearch.remote-generator-worker-receipt.1"',
        "schema=$RECEIPT_SCHEMA",
        "task_queue=$TASK_QUEUE",
        "task_queue_verified_from_release=true",
        "physical_host=$PHYSICAL_HOST",
        "gpu_uuid=$GPU_UUID",
        "gpu_preflight=$GPU_PREFLIGHT_STATUS",
        "gpu_memory_used_mib=$GPU_MEMORY_USED_MIB",
        "release_sha256=$EXPECTED_RELEASE",
        "source_revision=$SOURCE_REVISION",
        "launcher_sha256=$LAUNCHER_SHA256",
        "python_sha256=$PYTHON_SHA256",
        "environment_sha256=$ENVIRONMENT_SHA256",
        "service_tunnel_preflight=passed",
        "postgresql_endpoint=127.0.0.1:55432",
        "temporal_endpoint=127.0.0.1:17233",
        "object_store_endpoint=http://127.0.0.1:19000",
        "model_revision=$MODEL_REVISION",
        "weights_sha256=$WEIGHTS_SHA256",
        "started_at_utc=$STARTED_AT_UTC",
        "worker_command=$PYTHON -m pepagent.workers.v38_temporal_worker",
    ):
        assert receipt_field in text
    assert 'sha256sum "$RECEIPT_FILE" >"$RUN_DIR/worker.receipt.sha256"' in text
    assert "worker process environment differs from its frozen launch contract" in text
    assert "worker GPU placement differs from its frozen launch contract" in text
    assert "cleanup_failed_launch" in text
    assert "A failed" in text and "unreceipted poller" in text


def test_v38_remote_launcher_never_replaces_a_live_process() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "replacement requires external exact-ownership migration" in text
    assert "kill -0" in text
    assert "kill -9" not in text
    assert "pkill" not in text
    assert 'RECEIPT_SCHEMA="v38.remote-worker-receipt.1"' in text
    assert "ampgent_owned=true" in text
    assert "foreign=false" in text
    assert "runtime-cache-attestation.json" in text
    assert "runtime_cache_attestation_sha256=" in text


def test_gpu_declaration_scan_skips_unreadable_foreign_process_environments() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    declaration_scan = text[
        text.index('for declaration_process in /proc/[0-9]*') :
        text.index('GPU_MEMORY_TOTAL_MIB=', text.index('for declaration_process in /proc/[0-9]*'))
    ]
    assert '2>/dev/null' in declaration_scan
    assert 'head -n 1 || true' in declaration_scan
    assert "GPU has a CUDA_VISIBLE_DEVICES declaration; refusing launch" in declaration_scan
