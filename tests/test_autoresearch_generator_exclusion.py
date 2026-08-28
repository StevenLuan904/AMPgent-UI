from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from pepagent.autoresearch_closed_loop import DeNovoAction, PepMLMTargetedAction
from pepagent.provenance.hashing import sha256_text
from pepagent.workers import autoresearch_activities
from pepagent.workers.v38_temporal_worker import _max_concurrent_activities_for_role


def test_autoresearch_generator_worker_forces_one_activity_slot() -> None:
    assert _max_concurrent_activities_for_role("autoresearch-generator", 16) == 1
    assert _max_concurrent_activities_for_role("autoresearch-control", 16) == 16
    with pytest.raises(ValueError, match="concurrency must be positive"):
        _max_concurrent_activities_for_role("autoresearch-generator", 0)


@pytest.mark.asyncio
async def test_autoresearch_generator_batches_are_serialized(monkeypatch, tmp_path) -> None:
    entered: list[str] = []
    active = 0
    maximum_active = 0
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def fake_unlocked(request):
        nonlocal active, maximum_active
        run_id = request["action_plan"]["run_id"]
        entered.append(run_id)
        active += 1
        maximum_active = max(maximum_active, active)
        if run_id == "first":
            first_entered.set()
            await release_first.wait()
        active -= 1
        return {"run_id": run_id}

    monkeypatch.setattr(
        autoresearch_activities,
        "_AUTORESEARCH_GENERATOR_SEMAPHORE",
        asyncio.Semaphore(1),
    )
    monkeypatch.setattr(
        autoresearch_activities,
        "_execute_autoresearch_action_batch_unlocked",
        fake_unlocked,
    )
    monkeypatch.setattr(
        autoresearch_activities,
        "get_settings",
        lambda: SimpleNamespace(work_root=str(tmp_path)),
    )

    first = asyncio.create_task(
        autoresearch_activities.execute_autoresearch_action_batch(
            {"action_plan": {"run_id": "first", "iteration_no": 0}}
        )
    )
    await asyncio.wait_for(first_entered.wait(), timeout=2)
    second = asyncio.create_task(
        autoresearch_activities.execute_autoresearch_action_batch(
            {"action_plan": {"run_id": "second", "iteration_no": 0}}
        )
    )
    await asyncio.sleep(0.05)
    assert entered == ["first"]
    assert maximum_active == 1

    release_first.set()
    assert await first == {"run_id": "first"}
    assert await second == {"run_id": "second"}
    assert entered == ["first", "second"]
    assert maximum_active == 1


@pytest.mark.asyncio
async def test_pepmlm_executor_environment_is_provenance_not_a_runtime_gate(
    monkeypatch, tmp_path
) -> None:
    """A declared environment identity must not reject a valid model invocation."""

    target_sequence = "MKTIIALSYIFCLVFADYKDDDDK"
    action = PepMLMTargetedAction(
        branch_key="FGF2",
        generation=1,
        seed=41,
        operator_id="pepmlm-targeted-action-v1",
        operator_release_sha256="a" * 64,
        target_sequence_sha256=sha256_text(target_sequence),
        expected_improvement_metrics=("macrel_amp_probability",),
        protected_metrics=("guruprasad_instability_index",),
        evidence_sha256s=("b" * 64,),
        proposal_mode="de_novo",
        peptide_length=10,
    )
    action_id = str(uuid.uuid4())
    request = {
        "action_plan": {
            "run_id": str(uuid.uuid4()),
            "iteration_no": 0,
            "action_batch_sha256": "c" * 64,
            "actions": [
                {
                    "action_id": action_id,
                    "repository_action_sha256": "e" * 64,
                    "runtime_action_sha256": action.action_sha256,
                    "runtime_action": action.model_dump(mode="json"),
                    "lineage_sources": [],
                }
            ],
        },
        "executor": {
            # This is intentionally unrelated to the test process environment. It is
            # retained as request provenance, not re-proved from site-packages bytes.
            "operator_environment_sha256": "d" * 64,
            "target_sequence": target_sequence,
        },
    }

    class EmptySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    verified_model: list[tuple[str, str]] = []

    async def fake_verify(model_path: str, weights_sha256: str) -> None:
        verified_model.append((model_path, weights_sha256))

    async def fake_run_json_cli(module, payload, work_dir, *args):
        assert module == "pepagent.model_workers.pepmlm_cli"
        assert payload["model"] == "model://pepmlm-test"
        return {
            "candidates": [
                {
                    "action_id": action_id,
                    "sequence": "KRWLAKIRKL",
                    "action_sha256": action.action_sha256,
                    "sampling_seed": action.seed,
                    "sampling_attempt": 0,
                    "proposal_mode": "de_novo",
                }
            ]
        }

    settings = SimpleNamespace(
        pepmlm_model_path="model://pepmlm-test",
        pepmlm_model_revision="revision-test",
        pepmlm_weights_sha256="f" * 64,
        work_root=str(tmp_path),
    )
    monkeypatch.setattr(autoresearch_activities, "SessionFactory", EmptySession)
    monkeypatch.setattr(autoresearch_activities, "get_settings", lambda: settings)
    monkeypatch.setattr(autoresearch_activities, "_verify_pepmlm_release", fake_verify)
    monkeypatch.setattr(autoresearch_activities, "_run_json_cli", fake_run_json_cli)
    monkeypatch.setattr(
        autoresearch_activities.activity,
        "info",
        lambda: SimpleNamespace(attempt=1),
    )

    result = await autoresearch_activities._execute_autoresearch_action_batch_unlocked(
        request
    )

    assert verified_model == [(settings.pepmlm_model_path, settings.pepmlm_weights_sha256)]
    assert result["results"][0]["sequence"] == "KRWLAKIRKL"
    assert result["provenance"]["environment_sha256"] == "d" * 64
    assert result["provenance"]["model_uri"] == settings.pepmlm_model_path
    assert result["provenance"]["weights_sha256"] == settings.pepmlm_weights_sha256


@pytest.mark.asyncio
async def test_executor_preserves_duplicate_proposals_for_persistence_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sequence = "KRWLAKIRKL"
    actions = [
        DeNovoAction(
            branch_key="VEGFA",
            generation=1,
            seed=seed,
            operator_id="agent-de-novo-v1",
            operator_release_sha256="a" * 64,
            expected_improvement_metrics=("macrel_amp_probability",),
            protected_metrics=("guruprasad_instability_index",),
            evidence_sha256s=("b" * 64,),
            peptide_length=len(sequence),
            proposed_sequence=sequence,
        )
        for seed in (41, 43)
    ]
    request = {
        "action_plan": {
            "run_id": str(uuid.uuid4()),
            "iteration_no": 0,
            "action_batch_sha256": "c" * 64,
            "actions": [
                {
                    "action_id": str(uuid.uuid4()),
                    "repository_action_sha256": chr(ord("d") + index) * 64,
                    "runtime_action_sha256": action.action_sha256,
                    "runtime_action": action.model_dump(mode="json"),
                    "lineage_sources": [],
                }
                for index, action in enumerate(actions)
            ],
        },
        "executor": {
            "operator_environment_sha256": "f" * 64,
            "model_uri": "rules://de-novo-test",
        },
    }

    class EmptySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(autoresearch_activities, "SessionFactory", EmptySession)
    monkeypatch.setattr(
        autoresearch_activities.activity,
        "info",
        lambda: SimpleNamespace(attempt=1),
    )

    result = await autoresearch_activities._execute_autoresearch_action_batch_unlocked(
        request
    )

    assert [item["sequence"] for item in result["results"]] == [sequence, sequence]
    assert result["schema_version"] == "ampgent.autoresearch-materialized-action-batch.2"
    assert result["provenance"]["tool_version"] == "2"
