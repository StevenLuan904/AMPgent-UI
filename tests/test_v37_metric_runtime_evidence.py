import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_ROOT = ROOT / "config" / "environments" / "v37_metric_runtimes"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, object]:
    payload = path.read_bytes().decode("utf-8")
    assert "D:\\" not in payload
    assert "\ufffd" not in payload
    value = json.loads(payload)
    assert isinstance(value, dict)
    return value


def test_v37_metric_package_locks_are_exact() -> None:
    expected = {
        "llamp_amp_read.packages.lock.txt": (
            "6f850f20aca0439fc2e6b79a459a8dc1fe1058a6bbd1c2034105f7bd4bf05208",
            35,
        ),
        "toxinpred3.packages.lock.txt": (
            "6a4bbd9342e080362118fc9a44b2f1ad68760b356e170cf64b3086896d7a574a",
            5,
        ),
        "macrel_inner.packages.lock.txt": (
            "d8166f1bb7aafffb1931611efaec4b675320631b71be94d829f4ba2681f0c1d1",
            18,
        ),
    }
    for name, (expected_sha256, expected_count) in expected.items():
        path = ENV_ROOT / name
        lines = path.read_text(encoding="utf-8").splitlines()
        assert _sha256(path) == expected_sha256
        assert len(lines) == expected_count
        assert lines == sorted(lines)
        assert all(line.count("==") == 1 for line in lines)


def test_v37_metric_environment_evidence_is_fail_closed() -> None:
    shared = _load(ENV_ROOT / "shared_llamp_amp_read.environment.json")
    assert shared["schema_version"] == "v37.metric-runtime-environment.1"
    assert shared["registry_binding"] == {
        "registry_path": "config/metrics/runtime.local.yaml",
        "registry_sha256": (
            "007afeabe9302c7ac00e95f52e7fa00297946483982c39c12de8a62b5e6da6f9"
        ),
        "plugins": ["mic_potency", "mic_potency_amp_read"],
        "same_command_executable_proven": True,
    }
    assert shared["packages"]["pip_freeze_all_local_available"] is True
    assert shared["packages"]["pip_check"] == "No broken requirements found."

    toxinpred3 = _load(ENV_ROOT / "toxinpred3.environment.json")
    assert toxinpred3["packages"]["pip_available"] is False
    assert toxinpred3["packages"]["dependency_check"]["status"] == "ok"

    macrel = _load(ENV_ROOT / "macrel.environment.json")
    assert macrel["controller"]["pip_available"] is False
    assert macrel["inner_runtime"]["pip_available"] is False
    assert macrel["inner_runtime"]["dependency_check"]["status"] == "ok"
    assert macrel["controller"]["uv_lock_sha256"] == (
        "93f507654f1174f4510230756b0e791a8bc6aae8ef0de1c71d17f5e813822a62"
    )


def test_amp_read_manifest_binds_source_models_and_shared_environment() -> None:
    path = (
        ROOT
        / "config"
        / "metrics"
        / "manifests"
        / "amp_read_ec9478b_open_weights_cpu_v1.json"
    )
    manifest = _load(path)
    assert manifest["metric_plugin"] == "mic_potency_amp_read"
    assert manifest["decision_role"] == "soft_supporting_evidence_only"
    assert manifest["source"]["commit"] == (
        "ec9478b5e0d0aff4f92d6381c5027855390104c4"
    )
    assert manifest["source"]["model_source_sha256"] == (
        "1bed5830995e9d2f47bcbf64249d7e53ddb90eca83cc5f80b5ab9d6901220ba1"
    )
    assert {item["name"]: item["sha256"] for item in manifest["model_assets"]} == {
        "cnn": "da071c6624666e128aae79643dcbe6084fce339711b902a3ba89e4eb3aeee07e",
        "transformer": (
            "658a354dd0c76c1851516af361ea0899568642c02cc0fab0f6a55a87579fcc44"
        ),
        "attention": (
            "5dbea063bb56ffe7e903538aab4f4a90688d0f87f89c4ed26851fb649062283a"
        ),
        "lstm": "6f6d2784149a77fe430125aad612efe20cd3ef4e9b6680b5ec29b10ac99f9594",
    }
    assert manifest["runtime"]["environment_manifest"] == (
        "config/environments/v37_metric_runtimes/"
        "shared_llamp_amp_read.environment.json"
    )
    assert any("not calibration" in item for item in manifest["limitations"])
