from uuid import uuid4

import pytest

from pepagent.provenance.hashing import sha256_text
from pepagent.v38_sequence_first_multitarget import MultiTargetStructureTask, TargetBranchSpec
from pepagent.workers import v38_activities
from pepagent.workers.v38_temporal_worker import V38_ROLE_CONFIG

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
TARGET_SEQUENCE = "ACDEFGHIKLMNPQRSTVWY"


def _branch() -> TargetBranchSpec:
    return TargetBranchSpec(
        target_key="ec_gyrA_lei800",
        target_id=uuid4(),
        target_sequence_sha256=sha256_text(TARGET_SEQUENCE),
        coordinate_sha256=SHA_A,
        native_pocket_sha256=SHA_B,
        wrong_pocket_sha256=SHA_C,
        qualification_witness_sha256=SHA_D,
        evidence_grade="A",
        panel_role="qualified_target",
        structure_budget=48,
        boltz_seeds_per_candidate=3,
        rosetta_decoys_per_pose=16,
    )


def _request() -> dict:
    branch = _branch()
    candidate_id = uuid4()
    task = MultiTargetStructureTask(
        target_key=branch.target_key,
        target_id=branch.target_id,
        candidate_id=candidate_id,
        parallel_wave=0,
        control_lane="native",
        pocket_sha256=branch.native_pocket_sha256,
        boltz_seed=20270380,
        rosetta_decoys_per_pose=16,
        evidence_namespace=f"target/{branch.target_key}/{branch.target_id}/native",
        ordinal=0,
    )
    return {
        "run_id": str(uuid4()),
        "candidate": {"id": str(candidate_id), "sequence": "KKLLKKLLKKLL"},
        "structure_task": task.model_dump(mode="json"),
        "target_branch": branch.model_dump(mode="json"),
        "target_sequence": TARGET_SEQUENCE,
        "pocket_definition_sha256": branch.native_pocket_sha256,
        "pocket_residues": [1, 2, 3],
        "structure_spec": {"diffusion_samples": 1},
    }


@pytest.mark.asyncio
async def test_v38_boltz_wrapper_scopes_target_lane_and_exact_task(monkeypatch) -> None:
    request = _request()
    observed: dict = {}

    async def fake_predict(payload: dict) -> dict:
        observed.update(payload)
        return {
            "candidate": payload["candidate"],
            "input": {"seed": payload["seed"]},
            "parameters": {},
            "boltz2": {},
            "provenance": {},
        }

    monkeypatch.setattr(v38_activities, "predict_boltz2_complex", fake_predict)
    result = await v38_activities.predict_v38_multitarget_structure(request)
    task = MultiTargetStructureTask.model_validate(request["structure_task"])

    assert observed["work_scope"] == [str(task.target_id), "native", task.sha256()]
    assert observed["spec"]["target"]["sequence"] == TARGET_SEQUENCE
    assert observed["spec"]["target"]["pocket_residues"] == [1, 2, 3]
    assert result["v38_structure_task_sha256"] == task.sha256()


@pytest.mark.asyncio
async def test_v38_rosetta_wrapper_enforces_sixteen_decoys_and_same_scope(monkeypatch) -> None:
    request = _request()
    task = MultiTargetStructureTask.model_validate(request["structure_task"])
    request["structure"] = {
        "candidate": request["candidate"],
        "v38_structure_task_sha256": task.sha256(),
    }
    observed: dict = {}

    async def fake_score(payload: dict) -> dict:
        observed.update(payload)
        return {
            "candidate": payload["candidate"],
            "parameters": {"nstruct": payload["spec"]["rosetta_nstruct"]},
            "rosetta": {},
            "provenance": {},
        }

    monkeypatch.setattr(v38_activities, "score_rosetta_complex", fake_score)
    result = await v38_activities.score_v38_multitarget_rosetta(request)

    assert observed["spec"]["rosetta_nstruct"] == 16
    assert observed["work_scope"] == [str(task.target_id), "native", task.sha256()]
    assert result["v38_structure_task_sha256"] == task.sha256()


@pytest.mark.asyncio
async def test_v38_structure_wrapper_rejects_wrong_control_pocket() -> None:
    request = _request()
    request["pocket_definition_sha256"] = SHA_C
    with pytest.raises(ValueError, match="pocket definition SHA drifted"):
        await v38_activities.predict_v38_multitarget_structure(request)


def test_v38_worker_roles_isolate_generator_metrics_and_structure_queues() -> None:
    assert set(V38_ROLE_CONFIG) == {
        "v38-control",
        "v38-generator",
        "v38-metrics",
        "v38-boltz",
        "v38-rosetta",
    }
    queues = {role: config[0] for role, config in V38_ROLE_CONFIG.items()}
    assert len(set(queues.values())) == len(queues)
    assert queues["v38-boltz"] == "pepagent-gpu-boltz2-v38"
    assert queues["v38-rosetta"] == "pepagent-cpu-rosetta-v38"
