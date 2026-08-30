from uuid import uuid4

import pytest

from pepagent.provenance.hashing import sha256_text
from pepagent.v38_sequence_first_multitarget import (
    MultiTargetExecutionPlan,
    MultiTargetStructureTask,
    SequenceCohortAdmission,
    TargetBranchSpec,
)
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
        "v39-target-sequence",
        "v38-boltz",
        "v38-rosetta",
        "autoresearch-control",
        "autoresearch-generator",
        "autoresearch-persistence",
        "autoresearch-metrics",
        "autoresearch-cpu-successor-control",
        "autoresearch-cpu-successor-persistence",
        "autoresearch-cpu-successor-metrics",
        "autoresearch-cpu-successor-v2-control",
        "autoresearch-cpu-successor-v2-persistence",
        "autoresearch-cpu-successor-v2-metrics",
    }
    queues = {role: config[0] for role, config in V38_ROLE_CONFIG.items()}
    assert len(set(queues.values())) == len(queues)
    assert queues["v38-boltz"] == "pepagent-gpu-boltz2-v38"
    assert queues["v38-rosetta"] == "pepagent-cpu-rosetta-v38"
    assert queues["v39-target-sequence"] == "pepagent-gpu-target-sequence-v39"


def test_v38_structure_evidence_builders_bind_pose_and_all_decoys() -> None:
    request = _request()
    task = MultiTargetStructureTask.model_validate(request["structure_task"])
    boltz_result = {
        "v38_structure_task": task.model_dump(mode="json"),
        "v38_structure_task_sha256": task.sha256(),
        "tool_call_id": str(uuid4()),
        "parameters": {"seed": task.boltz_seed},
        "provenance": {
            "raw_output_artifact": {"sha256": SHA_D},
            "engine_artifacts": [{"path": "model_0.cif", "sha256": SHA_A}],
        },
    }
    boltz = v38_activities.build_v38_boltz_evidence(boltz_result)
    prepared_sha = "e" * 64
    prepacked_sha = "f" * 64
    decoys = [
        {
            "input_sha256": prepacked_sha,
            "output_sha256": f"{index + 1:064x}",
            "score_terms_sha256": f"{index + 100:064x}",
            "total_score": float(-index),
        }
        for index in range(16)
    ]
    rosetta_result = {
        "v38_structure_task": task.model_dump(mode="json"),
        "v38_structure_task_sha256": task.sha256(),
        "tool_call_id": str(uuid4()),
        "rosetta": {
            "input_sha256": SHA_A,
            "prepared_input_sha256": prepared_sha,
            "prepacked_input_sha256": prepacked_sha,
            "decoys": decoys,
        },
        "provenance": {
            "source_coordinate_artifact": {"sha256": SHA_A},
            "raw_output_artifact": {"sha256": SHA_C},
        },
    }
    rosetta = v38_activities.build_v38_rosetta_evidence(rosetta_result, boltz)

    assert boltz.coordinate_artifact_sha256 == SHA_A
    assert rosetta.boltz_evidence_sha256 == boltz.sha256()
    assert rosetta.converted_input_artifact_sha256 == SHA_A
    assert rosetta.prepared_input_artifact_sha256 == prepared_sha
    assert rosetta.prepacked_input_artifact_sha256 == prepacked_sha
    assert len(rosetta.decoys) == 16
    assert rosetta.decoys[15].decoy_ordinal == 15


def test_v38_rosetta_evidence_builder_rejects_incomplete_decoy_budget() -> None:
    request = _request()
    task = MultiTargetStructureTask.model_validate(request["structure_task"])
    boltz = v38_activities.build_v38_boltz_evidence(
        {
            "v38_structure_task": task.model_dump(mode="json"),
            "v38_structure_task_sha256": task.sha256(),
            "tool_call_id": str(uuid4()),
            "parameters": {"seed": task.boltz_seed},
            "provenance": {
                "raw_output_artifact": {"sha256": SHA_D},
                "engine_artifacts": [{"path": "model_0.cif", "sha256": SHA_A}],
            },
        }
    )
    result = {
        "v38_structure_task": task.model_dump(mode="json"),
        "v38_structure_task_sha256": task.sha256(),
        "tool_call_id": str(uuid4()),
        "rosetta": {
            "input_sha256": SHA_A,
            "prepared_input_sha256": "e" * 64,
            "prepacked_input_sha256": "f" * 64,
            "decoys": [
                {
                    "input_sha256": "f" * 64,
                    "output_sha256": f"{index + 1:064x}",
                    "score_terms_sha256": f"{index + 100:064x}",
                    "total_score": float(index),
                }
                for index in range(15)
            ]
        },
        "provenance": {
            "source_coordinate_artifact": {"sha256": SHA_A},
            "raw_output_artifact": {"sha256": SHA_C},
        },
    }
    with pytest.raises(ValueError, match="decoy count"):
        v38_activities.build_v38_rosetta_evidence(result, boltz)


def test_v38_task_plan_covers_candidates_targets_controls_and_seeds() -> None:
    candidate_ids = (uuid4(), uuid4())
    first = _branch()
    second = _branch().model_copy(
        update={"target_key": "se_pbp2a_allosteric", "target_id": uuid4()}
    )
    admission = SequenceCohortAdmission(
        refinement_round=0,
        decisions=(),
        mature_core_candidate_ids=(candidate_ids[0],),
        exploration_candidate_ids=(candidate_ids[1],),
        rejected_candidate_ids=(),
        refinement_required=False,
        structure_dispatch_allowed=True,
        unused_structure_slots=46,
    )
    cohort_sha = "1" * 64
    plan = MultiTargetExecutionPlan(
        harness_release_id="v38-test",
        history_snapshot_sha256="2" * 64,
        shared_sequence_cohort_sha256=cohort_sha,
        sequence_maturity_decision_sha256="3" * 64,
        target_branches=(first, second),
        max_parallel_targets=2,
    )
    result = v38_activities.build_v38_multitarget_task_plan(
        execution_plan=plan,
        admission_payload={
            "candidate_evidence_sha256": cohort_sha,
            "admission": admission.model_dump(mode="json"),
        },
        boltz_seeds=(20270380, 20270381, 20270382),
    )

    assert result["task_count"] == 24
    identities = {
        (
            item["candidate_id"],
            item["target_key"],
            item["control_lane"],
            item["boltz_seed"],
        )
        for item in result["tasks"]
    }
    assert len(identities) == 24
    assert {item["control_lane"] for item in result["tasks"]} == {
        "native",
        "wrong_pocket",
    }


def test_v38_task_plan_rejects_unconcluded_admission() -> None:
    branch_a = _branch()
    branch_b = _branch().model_copy(
        update={"target_key": "se_pbp2a_allosteric", "target_id": uuid4()}
    )
    cohort_sha = "1" * 64
    plan = MultiTargetExecutionPlan(
        harness_release_id="v38-test",
        history_snapshot_sha256="2" * 64,
        shared_sequence_cohort_sha256=cohort_sha,
        sequence_maturity_decision_sha256="3" * 64,
        target_branches=(branch_a, branch_b),
        max_parallel_targets=2,
    )
    admission = SequenceCohortAdmission(
        refinement_round=0,
        decisions=(),
        mature_core_candidate_ids=(uuid4(),),
        exploration_candidate_ids=(),
        rejected_candidate_ids=(),
        refinement_required=True,
        structure_dispatch_allowed=False,
        unused_structure_slots=48,
    )
    with pytest.raises(ValueError, match="concluded sequence admission"):
        v38_activities.build_v38_multitarget_task_plan(
            execution_plan=plan,
            admission_payload={
                "candidate_evidence_sha256": cohort_sha,
                "admission": admission.model_dump(mode="json"),
            },
            boltz_seeds=(20270380, 20270381, 20270382),
        )
