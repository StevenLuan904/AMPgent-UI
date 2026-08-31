from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from pepagent.model_workers.sequence_metric_plan import (
    MetricExecutionPlanError,
    build_external_metric_plan,
    consume_external_metric_result,
    execute_external_metric_plan,
    load_external_metric_adapter,
    materialize_external_metric_input,
    validate_external_metric_plan,
)


def _plan(tmp_path: Path) -> dict[str, object]:
    return build_external_metric_plan(
        plugin_name="mic_potency",
        adapter={
            "version": "llamp-test-v1",
            "command": [
                "locked-python",
                "adapter.py",
                "--input",
                "{input}",
                "--output",
                "{output}",
                "--raw-output-dir",
                "{raw_output_dir}",
                "--run-id",
                "{run_id}",
            ],
            "working_directory": "locked-release",
            "environment": {"CUDA_VISIBLE_DEVICES": ""},
            "timeout_seconds": 37,
            "source_revision": "source-revision",
            "model_uri": "registry://llamp/test",
            "weights_sha256": "a" * 64,
            "model_config_sha256": "b" * 64,
            "limitations": ["soft prediction only"],
        },
        work_dir=tmp_path / "work",
        run_id="run-1",
        registry_path=tmp_path / "registry.yaml",
        registry_sha256="c" * 64,
    )


def test_build_external_metric_plan_exposes_guardable_inventory_without_io(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)

    assert plan["executable"] == "locked-python"
    assert plan["arguments"][-1] == "run-1"
    assert plan["working_directory"] == "locked-release"
    assert plan["environment"] == {"CUDA_VISIBLE_DEVICES": ""}
    assert plan["inherits_parent_environment"] is False
    assert plan["source_inventory"]["source_revision"] == "source-revision"
    assert plan["source_inventory"]["declared_digest_inventory"] == {
        "model_config_sha256": "b" * 64,
        "weights_sha256": "a" * 64,
    }
    assert plan["model_inventory"] == {
        "model_uri": "registry://llamp/test",
        "weights_sha256": "a" * 64,
        "model_config_sha256": "b" * 64,
        "inventory": [],
    }
    assert not (tmp_path / "work").exists()
    validate_external_metric_plan(plan)


def test_external_metric_plan_rejects_parent_environment_inheritance(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan["inherits_parent_environment"] = True

    with pytest.raises(MetricExecutionPlanError, match="must isolate the provider runtime"):
        validate_external_metric_plan(plan)


def test_invalid_command_template_fails_before_any_provider_execution(
    tmp_path: Path,
) -> None:
    with pytest.raises(MetricExecutionPlanError, match="no command array"):
        build_external_metric_plan(
            plugin_name="mic_potency",
            adapter={"command": []},
            work_dir=tmp_path,
            run_id="run-1",
            registry_path=None,
            registry_sha256=None,
        )


def test_load_external_metric_adapter_binds_registry_digest(tmp_path: Path) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "adapters:\n  mic_potency:\n    enabled: true\n    command: [python, adapter.py]\n",
        encoding="utf-8",
    )

    adapter, digest = load_external_metric_adapter(registry, "mic_potency")

    assert adapter == {"enabled": True, "command": ["python", "adapter.py"]}
    assert digest is not None and len(digest) == 64


def test_materialize_removes_stale_output_and_execution_receipt_is_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "worker-python-lib")
    monkeypatch.setenv("VIRTUAL_ENV", "worker-venv")
    plan = _plan(tmp_path)
    output_path = Path(plan["output"]["predictions_csv"])
    output_path.parent.mkdir(parents=True)
    output_path.write_text("stale", encoding="utf-8")
    candidates = [{"id": "candidate-1", "sequence": "KLLKK"}]

    input_receipt = materialize_external_metric_input(plan, candidates)
    observed: dict[str, object] = {}

    def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
        observed["argv"] = argv
        observed.update(kwargs)
        return SimpleNamespace(returncode=9, stdout="provider-out", stderr="provider-err")

    execution_receipt = execute_external_metric_plan(plan, runner=runner)

    assert not output_path.exists()
    assert input_receipt["candidate_count"] == 1
    assert len(input_receipt["input_sha256"]) == 64
    assert observed["argv"] == plan["command_argv"]
    assert observed["cwd"] == "locked-release"
    assert observed["timeout"] == 37
    assert observed["env"]["CUDA_VISIBLE_DEVICES"] == ""
    assert observed["encoding"] == "utf-8"
    assert observed["errors"] == "replace"
    assert observed["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert observed["env"]["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONPATH" not in observed["env"]
    assert "VIRTUAL_ENV" not in observed["env"]
    assert execution_receipt["returncode"] == 9


def test_consume_validates_exact_candidate_sequence_mapping(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    candidates = [{"id": "candidate-1", "sequence": "KLLKK"}]
    materialize_external_metric_input(plan, candidates)
    output_path = Path(plan["output"]["predictions_csv"])
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "candidate_id",
                "sequence",
                "status",
                "llamp_log10_mic_um",
                "llamp_predicted_mic_um",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "candidate_id": "candidate-1",
                "sequence": "KLLKK",
                "status": "success",
                "llamp_log10_mic_um": "1.0",
                "llamp_predicted_mic_um": "10.0",
            }
        )

    result = consume_external_metric_result(
        plan=plan,
        candidates=candidates,
        execution_receipt={
            "status": "completed",
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
        },
    )

    assert result["status"] == "complete"
    assert result["records"][0]["candidate_id"] == "candidate-1"
    assert result["weights_sha256"] == "a" * 64

    candidates[0]["sequence"] = "DIFFERENT"
    rejected = consume_external_metric_result(
        plan=plan,
        candidates=candidates,
        execution_receipt={"status": "completed", "returncode": 0},
    )
    assert rejected["status"] == "unavailable"
    assert "sequence mismatch" in rejected["reason"]


def test_consume_rejects_provider_row_reordering(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    candidates = [
        {"id": "candidate-1", "sequence": "KLLKK"},
        {"id": "candidate-2", "sequence": "RLLRR"},
    ]
    materialize_external_metric_input(plan, candidates)
    output_path = Path(plan["output"]["predictions_csv"])
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "candidate_id",
                "sequence",
                "status",
                "llamp_log10_mic_um",
                "llamp_predicted_mic_um",
            ],
        )
        writer.writeheader()
        for candidate in reversed(candidates):
            writer.writerow(
                {
                    "candidate_id": candidate["id"],
                    "sequence": candidate["sequence"],
                    "status": "success",
                    "llamp_log10_mic_um": "1.0",
                    "llamp_predicted_mic_um": "10.0",
                }
            )

    result = consume_external_metric_result(
        plan=plan,
        candidates=candidates,
        execution_receipt={"status": "completed", "returncode": 0},
    )

    assert result["status"] == "unavailable"
    assert "exact order" in result["reason"]


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_consume_rejects_nonfinite_numeric_outputs(
    tmp_path: Path, value: str
) -> None:
    plan = _plan(tmp_path)
    candidates = [{"id": "candidate-1", "sequence": "KLLKK"}]
    materialize_external_metric_input(plan, candidates)
    output_path = Path(plan["output"]["predictions_csv"])
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "candidate_id",
                "sequence",
                "status",
                "llamp_log10_mic_um",
                "llamp_predicted_mic_um",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "candidate_id": "candidate-1",
                "sequence": "KLLKK",
                "status": "success",
                "llamp_log10_mic_um": value,
                "llamp_predicted_mic_um": "10.0",
            }
        )

    result = consume_external_metric_result(
        plan=plan,
        candidates=candidates,
        execution_receipt={"status": "completed", "returncode": 0},
    )

    assert result["status"] == "unavailable"
    assert "non-finite" in result["reason"]
