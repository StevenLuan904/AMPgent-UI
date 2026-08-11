import statistics
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_json, sha256_text
from pepagent.v37_evidence import build_v37_evidence_plan
from pepagent.v37_persistence import (
    _selection_witness_payloads,
    build_v37_artifact_contract,
    validate_v37_database_object_replay,
)
from pepagent.v37_preregistration import load_v37_preregistration
from pepagent.v37_provider_consumers import (
    PEPSHOT_INSPECT_CONTRACT_ID,
    PEPSHOT_INSPECTION_SCHEMA_SHA256,
    PEPSHOT_RELEASE_ID,
    PEPSHOT_RELEASE_MANIFEST_SHA256,
    PEPSHOT_REQUEST_SCHEMA_SHA256,
    PEPSHOT_RUNTIME_MANIFEST_SHA256,
)
from pepagent.v37_selection import select_v37_lanes

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_rapid_champion_generation_v37.yaml"


def _runtime_receipt(runtime_id: str) -> dict:
    identity = {"runtime_id": runtime_id, "fixture": True}
    identity_sha256 = sha256_json(identity)
    stages = {}
    for stage_name in ("pre_snapshot", "prelaunch", "post_spawn", "completion"):
        stage = {
            "stage": stage_name,
            "identity": dict(identity),
            "byte_identity_sha256": identity_sha256,
            "preflight_revalidated_at_launch_boundary": True,
        }
        stage["launch_receipt_sha256"] = sha256_json(stage)
        stages[stage_name] = stage
    receipt = {
        "schema_version": "v37.guarded-runtime-receipts.2",
        **stages,
        "byte_identity_sha256": identity_sha256,
        "all_boundaries_match": True,
        "returncode": 0,
    }
    receipt["launch_receipt_sha256"] = sha256_json(receipt)
    return receipt


def _fixture() -> tuple[dict, dict, dict, dict[str, dict]]:
    frozen = load_v37_preregistration(CONFIG)
    plan = build_v37_evidence_plan(frozen)
    manifest = frozen.model_dump(mode="python")
    manifest["generators"]["raw_proposals_per_generator_seed"] = 2
    manifest["generators"]["evaluated_valid_unique_per_generator_seed"] = 1
    manifest["stage_2_structure_confirmation"]["poses_per_candidate"] = 3
    manifest["stage_2_structure_confirmation"]["rosetta_decoys_per_pose"] = 2
    manifest["final_portfolio"]["total_quota"] = 1
    for index, lane in enumerate(manifest["final_portfolio"]["lanes"]):
        lane["quota"] = 1 if index == 0 else 0

    calls = []
    call_ids = {}
    artifacts = []
    links = []
    payloads: dict[str, dict] = {}
    candidates = []
    evaluations = []
    events = []
    contract = build_v37_artifact_contract(plan)
    all_calls = [
        *plan["generator_calls"],
        *plan["metric_calls"],
        *plan["global_calls"],
    ]
    for index, item in enumerate(all_calls, start=1):
        logical_id = item["logical_id"]
        call_id = f"call-{index}"
        call_ids[logical_id] = call_id
        input_json = {"v37_logical_id": logical_id}
        input_sha256 = sha256_json(input_json)
        output_sha256 = sha256_json({"logical_id": logical_id, "status": "fixture"})
        idempotency_key = sha256_json({"call_id": call_id})
        calls.append(
            {
                "id": call_id,
                "status": "succeeded",
                "tool_name": item["tool_name"],
                "input_json": input_json,
                "input_sha256": input_sha256,
                "output_sha256": output_sha256,
                "idempotency_key": idempotency_key,
                "random_seed": None,
                "parameters_json": {
                    "v37_plan_sha256": plan["plan_sha256"],
                    **(
                        {
                            "v37_plugin_name": item["plugin_name"],
                            "v37_metric_names": item["metric_names"],
                        }
                        if "plugin_name" in item
                        else {}
                    ),
                },
            }
        )
        event_payload = {
            "tool_call_id": call_id,
            "idempotency_key": idempotency_key,
            "input_sha256": input_sha256,
            "output_sha256": output_sha256,
        }
        events.append(
            {
                "event_type": "tool_call.succeeded",
                "payload_json": event_payload,
                "payload_sha256": sha256_json(event_payload),
            }
        )

    for index, generator in enumerate(plan["generator_calls"], start=1):
        logical_id = generator["logical_id"]
        candidate_id = f"candidate-{index:02d}"
        sequence = f"KRW{'K' * index}"
        sequence_sha = sha256_text(sequence)
        candidates.append(
            {
                "id": candidate_id,
                "sequence": sequence,
                "sequence_sha256": sequence_sha,
                "metadata": {
                    "generator_id": generator["generator_id"],
                    "generator_seed": generator["seed"],
                    "raw_rank": 1,
                },
            }
        )
        occurrences = [
            {
                "v37_logical_id": logical_id,
                "raw_rank": 1,
                "sequence": sequence,
                "sequence_sha256": sequence_sha,
                "valid": True,
                "duplicate": False,
                "retained": True,
                "candidate_id": candidate_id,
                "reason": None,
            },
            {
                "v37_logical_id": logical_id,
                "raw_rank": 2,
                "sequence": None,
                "sequence_sha256": None,
                "valid": False,
                "duplicate": False,
                "retained": False,
                "candidate_id": None,
                "reason": "invalid_sequence",
            },
        ]
        events.extend(
            {"event_type": "v37.proposal_occurrence", "payload_json": row}
            for row in occurrences
        )
        payloads[(logical_id, "proposal_occurrences")] = {
            "schema_version": "1.0",
            "occurrences": occurrences,
        }

    for metric in plan["metric_calls"]:
        logical_id = metric["logical_id"]
        rows = []
        for candidate in sorted(candidates, key=lambda row: row["id"]):
            for metric_name in metric["metric_names"]:
                label = {
                    "toxinpred3_label": "Non-Toxin",
                    "macrel_hemolysis_label": "low",
                }.get(metric_name)
                row = {
                    "candidate_id": candidate["id"],
                    "metric_name": metric_name,
                    "numeric_value": None if label is not None else 1.0,
                    "text_value": label,
                    "unit": None,
                    "status": "succeeded",
                    "out_of_domain": False,
                    "limitations": [],
                    "raw": {"source": "synthetic-test"},
                }
                rows.append(row)
                evaluations.append({"tool_call_id": call_ids[logical_id], **row})
        payloads[(logical_id, "evaluation_vector")] = {"evaluations": rows}

    metric_rows_by_candidate = {candidate["id"]: {} for candidate in candidates}
    label_rows_by_candidate = {candidate["id"]: {} for candidate in candidates}
    for row in evaluations:
        if row["numeric_value"] is not None:
            metric_rows_by_candidate[row["candidate_id"]][row["metric_name"]] = row[
                "numeric_value"
            ]
        if row["text_value"] is not None:
            label_rows_by_candidate[row["candidate_id"]][row["metric_name"]] = row[
                "text_value"
            ]
    shortlist_policy = manifest["stage_1_sequence_evaluation"]["shortlist"]
    selection = select_v37_lanes(
        [
            {
                "id": candidate["id"],
                "sequence": candidate["sequence"],
                "sequence_sha256": candidate["sequence_sha256"],
                "generator_id": candidate["metadata"]["generator_id"],
                "seed": candidate["metadata"]["generator_seed"],
                "source_ordinal": candidate["metadata"]["raw_rank"],
                "metrics": metric_rows_by_candidate[candidate["id"]],
                "labels": label_rows_by_candidate[candidate["id"]],
            }
            for candidate in candidates
        ],
        lanes=[
            {
                "name": name,
                "quota": quota,
                "objective_families": shortlist_policy["lane_objective_families"][
                    name
                ],
            }
            for name, quota in shortlist_policy["lane_quotas"].items()
        ],
        family_objectives=manifest["stage_1_sequence_evaluation"]["endpoint_families"],
        maximum_similarity=0.80,
        maximum_per_generator=6,
        maximum_per_generator_seed=2,
    )
    shortlist_ids = selection["selected_ids"]
    payloads[("v37:stage1-shortlist", "shortlist_manifest")] = {
        "candidate_ids": shortlist_ids,
        "selection": selection,
    }
    for role, witness in _selection_witness_payloads(selection).items():
        payloads[("v37:stage1-shortlist", role)] = witness
    physical_dependencies: list[dict] = []

    def add_physical_call(
        *,
        call_id: str,
        tool_name: str,
        candidate_id: str | None,
        input_json: dict,
        output_payload: dict,
        random_seed: int | None = None,
        artifact_sha256: str | None = None,
    ) -> dict:
        input_sha256 = sha256_json(input_json)
        output_sha256 = sha256_json(output_payload)
        idempotency_key = sha256_json({"call_id": call_id})
        row = {
            "id": call_id,
            "status": "succeeded",
            "tool_name": tool_name,
            "input_json": input_json,
            "input_sha256": input_sha256,
            "parameters_json": {},
            "output_sha256": output_sha256,
            "idempotency_key": idempotency_key,
            "random_seed": random_seed,
        }
        calls.append(row)
        event_payload = {
            "tool_call_id": call_id,
            "idempotency_key": idempotency_key,
            "input_sha256": input_sha256,
            "output_sha256": output_sha256,
        }
        events.append(
            {
                "event_type": "tool_call.succeeded",
                "payload_json": event_payload,
                "payload_sha256": sha256_json(event_payload),
            }
        )
        physical_metric_name = {
            "boltz2": "boltz2_pair_iptm",
            "coordinate-interface-audit": "pocket_coverage_fraction",
            "pyrosetta-flexpepdock-interface-analyzer": "rosetta_dg_separated_reu",
        }.get(tool_name)
        if candidate_id is not None and physical_metric_name is not None:
            evaluations.append(
                {
                    "tool_call_id": call_id,
                    "candidate_id": candidate_id,
                    "metric_name": physical_metric_name,
                    "numeric_value": 1.0,
                    "text_value": None,
                    "unit": None,
                    "status": "succeeded",
                    "out_of_domain": False,
                    "limitations": [],
                    "raw": {"fixture": True},
                }
            )
        if artifact_sha256 is not None:
            artifact_id = f"artifact-{len(artifacts) + 1}"
            artifacts.append({"id": artifact_id, "sha256": artifact_sha256})
            links.append(
                {
                    "tool_call_id": call_id,
                    "artifact_id": artifact_id,
                    "role": "engine_output_0",
                }
            )
            payloads.setdefault(artifact_sha256, {"fixture": artifact_sha256})
        return row

    poses = []
    for candidate_id in shortlist_ids:
        candidate = next(item for item in candidates if item["id"] == candidate_id)
        audit_call_id = f"{candidate_id}-audit"
        boltz_call_ids = []
        candidate_pose_rows = []
        for pose_rank, boltz_seed in enumerate(
            manifest["stage_2_structure_confirmation"]["boltz_seeds"], start=1
        ):
            pose_id = f"{candidate_id}-pose-{pose_rank}"
            coordinate_sha256 = sha256_json(
                {"candidate_id": candidate_id, "boltz_seed": boltz_seed}
            )
            boltz_call = add_physical_call(
                call_id=pose_id,
                tool_name="boltz2",
                candidate_id=candidate_id,
                input_json={
                    "peptide_sequence": candidate["sequence"],
                    "seed": boltz_seed,
                },
                output_payload={"pair_iptm": 0.5 + pose_rank / 10},
                random_seed=boltz_seed,
                artifact_sha256=coordinate_sha256,
            )
            boltz_call_ids.append(pose_id)
            candidate_pose_rows.append((pose_rank, boltz_seed, coordinate_sha256, boltz_call))
        audit_call = add_physical_call(
            call_id=audit_call_id,
            tool_name="coordinate-interface-audit",
            candidate_id=candidate_id,
            input_json={"candidate_id": candidate_id, "pose_ids": boltz_call_ids},
            output_payload={"candidate_id": candidate_id, "audited": boltz_call_ids},
        )
        for pose_id in boltz_call_ids:
            physical_dependencies.append(
                {
                    "child_tool_call_id": audit_call_id,
                    "parent_tool_call_id": pose_id,
                    "relation_type": "audits",
                }
            )
        for pose_rank, boltz_seed, coordinate_sha256, boltz_call in candidate_pose_rows:
            poses.append(
                {
                    "candidate_id": candidate_id,
                    "pose_id": f"{candidate_id}-pose-{pose_rank}",
                    "boltz_seed": boltz_seed,
                    "interface_audit_tool_call_id": audit_call_id,
                    "boltz_input_sha256": boltz_call["input_sha256"],
                    "boltz_output_sha256": boltz_call["output_sha256"],
                    "interface_audit_input_sha256": audit_call["input_sha256"],
                    "interface_audit_output_sha256": audit_call["output_sha256"],
                    "structure_sha256": coordinate_sha256,
                    "coordinate_audit_sha256": "b" * 64,
                    "pair_iptm": 0.5 + pose_rank / 10,
                    "pocket_coverage_fraction": 0.4 + pose_rank / 10,
                    "geometric_clash_count": pose_rank,
                    "peptide_backbone_displacement": pose_rank / 10,
                }
            )
    payloads[("v37:structure", "pose_manifest")] = {"poses": poses}
    decoys = []
    for pose in poses:
        rosetta_call_id = f"{pose['pose_id']}-rosetta"
        rosetta_call = add_physical_call(
            call_id=rosetta_call_id,
            tool_name="pyrosetta-flexpepdock-interface-analyzer",
            candidate_id=pose["candidate_id"],
            input_json={
                "candidate_id": pose["candidate_id"],
                "parent_tool_call_id": pose["pose_id"],
                "seed": pose["boltz_seed"],
            },
            output_payload={"pose_id": pose["pose_id"], "decoy_count": 2},
            random_seed=pose["boltz_seed"],
        )
        physical_dependencies.append(
            {
                "child_tool_call_id": rosetta_call_id,
                "parent_tool_call_id": pose["pose_id"],
                "relation_type": "refines",
            }
        )
        for decoy_rank in range(1, 3):
            decoys.append(
                {
                    "candidate_id": pose["candidate_id"],
                    "pose_id": pose["pose_id"],
                    "decoy_id": f"{pose['pose_id']}-decoy-{decoy_rank}",
                    "boltz_seed": pose["boltz_seed"],
                    "rosetta_tool_call_id": rosetta_call_id,
                    "rosetta_call_input_sha256": rosetta_call["input_sha256"],
                    "rosetta_call_output_sha256": rosetta_call["output_sha256"],
                    "interface_delta_g_reu": -float(decoy_rank),
                    "input_sha256": "c" * 64,
                    "output_sha256": "d" * 64,
                    "score_terms_sha256": "e" * 64,
                }
            )
    payloads[("v37:rosetta", "decoy_manifest")] = {"decoys": decoys}
    pepshot_contract_id = "pepshot-contract-call"
    add_physical_call(
        call_id=pepshot_contract_id,
        tool_name="pepshot-contract",
        candidate_id=None,
        input_json={"contract_id": PEPSHOT_INSPECT_CONTRACT_ID},
        output_payload={"valid": True, "contract_id": PEPSHOT_INSPECT_CONTRACT_ID},
    )
    pepshot_inspections = []
    pepshot_detail_ids = []
    for candidate_id in shortlist_ids:
        representative_pose = next(
            pose
            for pose in poses
            if pose["candidate_id"] == candidate_id and pose["boltz_seed"] == 20270381
        )
        request_sha256 = sha256_json(
            {
                "candidate_id": candidate_id,
                "pose_id": representative_pose["pose_id"],
                "coordinate_sha256": representative_pose["structure_sha256"],
            }
        )
        inspection_payload = {
            "candidate_id": candidate_id,
            "request_sha256": request_sha256,
            "interface_verdict": "PASS",
            "findings": [],
        }
        detail_call_id = f"{candidate_id}-pepshot-inspect"
        detail_call = add_physical_call(
            call_id=detail_call_id,
            tool_name="pepshot-inspect",
            candidate_id=candidate_id,
            input_json={
                "candidate_id": candidate_id,
                "request_sha256": request_sha256,
                "coordinate_sha256": representative_pose["structure_sha256"],
            },
            output_payload=inspection_payload,
        )
        pepshot_detail_ids.append(detail_call_id)
        physical_dependencies.extend(
            [
                {
                    "child_tool_call_id": detail_call_id,
                    "parent_tool_call_id": pepshot_contract_id,
                    "relation_type": "uses_verified_inspect_contract",
                },
                {
                    "child_tool_call_id": call_ids["v37:pepshot"],
                    "parent_tool_call_id": detail_call_id,
                    "relation_type": "aggregates_candidate_inspection",
                },
            ]
        )
        pepshot_inspections.append(
            {
                "candidate_id": candidate_id,
                "representative_pose_id": representative_pose["pose_id"],
                "boltz_seed": representative_pose["boltz_seed"],
                "disposition": "retain",
                "reason": "provider_inspect_interface_pass",
                "request_sha256": request_sha256,
                "inspection_id": sha256_json({"detail_call_id": detail_call_id}),
                "inspection_sha256": detail_call["output_sha256"],
                "source_sha256": representative_pose["structure_sha256"],
                "interface_verdict": "PASS",
                "contract_id": PEPSHOT_INSPECT_CONTRACT_ID,
                "request_schema_sha256": PEPSHOT_REQUEST_SCHEMA_SHA256,
                "inspection_schema_sha256": PEPSHOT_INSPECTION_SCHEMA_SHA256,
                "release_id": PEPSHOT_RELEASE_ID,
                "release_manifest_sha256": PEPSHOT_RELEASE_MANIFEST_SHA256,
                "runtime_manifest_sha256": PEPSHOT_RUNTIME_MANIFEST_SHA256,
                "spatial_finding_count": 0,
                "blocking_finding_types": [],
                "detail_tool_call_id": detail_call_id,
                "detail_input_sha256": detail_call["input_sha256"],
                "detail_output_sha256": detail_call["output_sha256"],
            }
        )
    payloads[("v37:pepshot", "pepshot_evidence")] = {
        "inspections": pepshot_inspections
    }
    payloads[("v37:pepshot", "provider_release_receipt")] = {
        "contract_tool_call_id": pepshot_contract_id,
        "detail_tool_call_ids": pepshot_detail_ids,
        "release_id": PEPSHOT_RELEASE_ID,
    }
    for call_id in [pepshot_contract_id, *pepshot_detail_ids]:
        receipt = _runtime_receipt(call_id)
        digest = sha256_json(receipt)
        artifact_id = f"artifact-{len(artifacts) + 1}"
        artifacts.append({"id": artifact_id, "sha256": digest})
        links.append(
            {
                "tool_call_id": call_id,
                "artifact_id": artifact_id,
                "role": "v37_runtime_receipts",
            }
        )
        payloads[digest] = receipt
        runtime_event_payload = {
            "tool_call_id": call_id,
            "runtime_id": "pepshot",
            "artifact_sha256": digest,
            "launch_receipt_sha256": receipt["launch_receipt_sha256"],
        }
        events.append(
            {
                "event_type": "v37.runtime_receipts.committed",
                "payload_json": runtime_event_payload,
                "payload_sha256": sha256_json(runtime_event_payload),
            }
        )
    physical_dependencies.extend(
        {
            "child_tool_call_id": call_ids["v37:structure"],
            "parent_tool_call_id": physical_call_id,
            "relation_type": "summarizes_structure_evidence",
        }
        for physical_call_id in {
            *(pose["pose_id"] for pose in poses),
            *(pose["interface_audit_tool_call_id"] for pose in poses),
        }
    )
    physical_dependencies.extend(
        {
            "child_tool_call_id": call_ids["v37:rosetta"],
            "parent_tool_call_id": decoy["rosetta_tool_call_id"],
            "relation_type": "summarizes_rosetta_evidence",
        }
        for decoy in {item["rosetta_tool_call_id"]: item for item in decoys}.values()
    )
    physical_dependencies.append(
        {
            "child_tool_call_id": call_ids["v37:rosetta"],
            "parent_tool_call_id": call_ids["v37:structure"],
            "relation_type": "scores_frozen_structure_stage",
        }
    )
    final_candidates = []
    by_candidate = {candidate["id"]: candidate for candidate in candidates}
    for candidate_id in shortlist_ids:
        candidate = by_candidate[candidate_id]
        candidate_poses = [
            pose for pose in poses if pose["candidate_id"] == candidate_id
        ]
        representative_scores = [
            statistics.median(
                decoy["interface_delta_g_reu"]
                for decoy in decoys
                if decoy["pose_id"] == pose["pose_id"]
            )
            for pose in candidate_poses
        ]
        displacements = [
            pose["peptide_backbone_displacement"] for pose in candidate_poses
        ]
        final_candidates.append(
            {
                "id": candidate_id,
                "sequence": candidate["sequence"],
                "sequence_sha256": candidate["sequence_sha256"],
                "generator_id": candidate["metadata"]["generator_id"],
                "seed": candidate["metadata"]["generator_seed"],
                "source_ordinal": candidate["metadata"]["raw_rank"],
                "metrics": {
                    **metric_rows_by_candidate[candidate_id],
                    "median_pair_iptm": statistics.median(
                        pose["pair_iptm"] for pose in candidate_poses
                    ),
                    "median_pocket_coverage": statistics.median(
                        pose["pocket_coverage_fraction"] for pose in candidate_poses
                    ),
                    "maximum_geometric_clash_count": float(
                        max(pose["geometric_clash_count"] for pose in candidate_poses)
                    ),
                    "peptide_backbone_displacement_range": max(displacements)
                    - min(displacements),
                    "median_representative_rosetta_interface_delta_g": statistics.median(
                        representative_scores
                    ),
                },
                "labels": label_rows_by_candidate[candidate_id],
            }
        )
    final_lanes = [
        {
            "name": lane["name"],
            "quota": lane["quota"],
            "objective_families": lane["Pareto_objective_families"],
            "required_soft_labels": lane.get("required_soft_labels", {}),
        }
        for lane in manifest["final_portfolio"]["lanes"]
    ]
    final_families = dict(
        manifest["stage_1_sequence_evaluation"]["endpoint_families"]
    )
    final_families["structure"] = manifest["stage_2_structure_confirmation"][
        "Pareto_objectives"
    ]
    final_selection = select_v37_lanes(
        final_candidates,
        lanes=final_lanes,
        family_objectives=final_families,
        maximum_similarity=0.80,
        maximum_per_generator=2,
        maximum_per_generator_seed=1,
    )
    payloads[("v37:final-portfolio", "final_portfolio")] = {
        "candidate_ids": final_selection["selected_ids"],
        "selection": final_selection,
        "candidate_summaries": {
            item["id"]: {"metrics": item["metrics"], "labels": item["labels"]}
            for item in final_candidates
        },
    }
    for role, witness in _selection_witness_payloads(final_selection).items():
        payloads[("v37:final-portfolio", role)] = witness
    payloads[("v37:knowledge", "knowledge_evidence")] = {
        "schema_version": "1.0",
        "query_sha256": "1" * 64,
        "query_pack_sha256": "2" * 64,
        "trace_sha256": "3" * 64,
        "policy_sha256": "4" * 64,
        "cards": [
            {
                "card_id": "card-1",
                "revision": "revision-1",
                "passage_manifest_sha256": "5" * 64,
            }
        ],
        "adoption_edges": [
            {
                "evidence_id": "card-1:passage-1",
                "disposition": "used",
                "reason": "applicable to frozen target context",
                "candidate_ids": [item["id"] for item in candidates],
            }
        ],
    }

    for stage in plan["global_calls"]:
        events.append(
            {
                "event_type": "v37.stage_stopped",
                "payload_json": {
                    "v37_logical_id": stage["logical_id"],
                    "stop_reason": "completed_frozen_budget",
                },
            }
        )

    committed_graph = {
        "schema_version": "v37.preclosure-evidence-graph.1",
        "run_id": "fixture-run",
    }
    committed_graph["graph_sha256"] = sha256_json(committed_graph)
    committed_snapshot = {
        "schema_version": "v37.committed-graph-snapshot.1",
        "committed_graph_sha256": committed_graph["graph_sha256"],
        "graph": committed_graph,
    }
    replay_payload = {
        "schema_version": "v37.database-object-replay.2",
        "committed_graph_sha256": committed_graph["graph_sha256"],
        "committed_graph_snapshot_sha256": sha256_json(committed_snapshot),
        "exact_replay": True,
    }
    payloads[("v37:replay", "database_object_replay")] = replay_payload
    payloads[("v37:replay", "committed_graph_snapshot")] = committed_snapshot
    replay_call = next(
        item for item in calls if item["id"] == call_ids["v37:replay"]
    )
    replay_call["output_sha256"] = sha256_json(replay_payload)
    replay_success_event = next(
        item
        for item in events
        if item["event_type"] == "tool_call.succeeded"
        and item["payload_json"]["tool_call_id"] == replay_call["id"]
    )
    replay_success_event["payload_json"]["output_sha256"] = replay_call[
        "output_sha256"
    ]
    replay_success_event["payload_sha256"] = sha256_json(
        replay_success_event["payload_json"]
    )

    for item in plan["generator_calls"]:
        logical_id = item["logical_id"]
        payloads[(logical_id, "source_runtime_receipt")] = {
            "live_launch_receipt": _runtime_receipt(logical_id),
            "runtime_identity": {"generator_id": item["generator_id"]},
        }
    for item in plan["metric_calls"]:
        logical_id = item["logical_id"]
        if logical_id == "v37:metric:physicochemical_developability":
            continue
        payloads[(logical_id, "source_runtime_receipt")] = {
            "provenance": {"live_launch_receipt": _runtime_receipt(logical_id)},
            "plugin": {"name": logical_id.removeprefix("v37:metric:")},
            "contract": {"fixture": True},
        }

    for logical_id, roles in contract.items():
        payloads[(logical_id, "attempt_ledger")] = {
            "schema_version": "1.0",
            "v37_logical_id": logical_id,
            "attempts": [{"attempt": 1, "status": "succeeded"}],
        }
        payloads[(logical_id, "failure_ledger")] = {
            "schema_version": "1.0",
            "v37_logical_id": logical_id,
            "failures": [],
        }
        for role in roles:
            payload = payloads.get(
                (logical_id, role), {"logical_id": logical_id, "role": role}
            )
            digest = sha256_json(payload)
            artifact_id = f"artifact-{len(artifacts) + 1}"
            artifacts.append({"id": artifact_id, "sha256": digest})
            links.append(
                {
                    "tool_call_id": call_ids[logical_id],
                    "artifact_id": artifact_id,
                    "role": role,
                }
            )
            payloads[digest] = payload
            receipt = (
                payload
                if role == "v37_runtime_receipts"
                else payload.get("provenance", {}).get("live_launch_receipt")
                if role == "source_runtime_receipt"
                else None
            )
            event_required = role == "v37_runtime_receipts" or (
                role == "source_runtime_receipt"
                and logical_id.startswith("v37:metric:")
                and logical_id != "v37:metric:physicochemical_developability"
            )
            if isinstance(receipt, dict) and event_required:
                runtime_event_payload = {
                    "tool_call_id": call_ids[logical_id],
                    "runtime_id": logical_id,
                    "artifact_sha256": digest,
                    "launch_receipt_sha256": receipt["launch_receipt_sha256"],
                }
                events.append(
                    {
                        "event_type": "v37.runtime_receipts.committed",
                        "payload_json": runtime_event_payload,
                        "payload_sha256": sha256_json(runtime_event_payload),
                    }
                )

    decisions = []
    decision_edges = []
    for index, logical_id in enumerate(
        (
            "v37:knowledge",
            "v37:stage1-shortlist",
            "v37:pepshot",
            "v37:final-portfolio",
            "v37:replay",
        ),
        start=1,
    ):
        decision_id = f"decision-{index}"
        decisions.append(
            {
                "id": decision_id,
                "decision_type": "v37_stage_decision",
                "status": "succeeded",
                "structured_json": {"v37_logical_id": logical_id},
            }
        )
        decision_edges.extend(
            [
                {
                    "decision_id": decision_id,
                    "tool_call_id": call_ids[plan["generator_calls"][0]["logical_id"]],
                    "direction": "input",
                    "relation_type": "observes_v37_stage_evidence",
                },
                {
                    "decision_id": decision_id,
                    "tool_call_id": call_ids[logical_id],
                    "direction": "output",
                    "relation_type": "materializes_v37_stage_decision",
                },
            ]
        )

    for event in events:
        event.setdefault("payload_sha256", sha256_json(event["payload_json"]))

    graph = {
        "tool_calls": calls,
        "tool_call_dependencies": [
            {
                "parent_tool_call_id": call_ids[parent],
                "child_tool_call_id": call_ids[child],
                "relation_type": "frozen_dependency",
            }
            for parent, child in plan["dependencies"]
        ]
        + physical_dependencies,
        "artifacts": artifacts,
        "evidence_artifacts": links,
        "candidates": candidates,
        "evaluations": evaluations,
        "lifecycle_events": events,
        "agent_decisions": decisions,
        "agent_decision_tool_call_edges": decision_edges,
    }
    return manifest, plan, graph, {
        key: value for key, value in payloads.items() if isinstance(key, str)
    }


def _validate(fixture: tuple[dict, dict, dict, dict[str, dict]]) -> dict:
    manifest, plan, graph, payloads = fixture
    return validate_v37_database_object_replay(
        manifest=manifest,
        plan=plan,
        graph=graph,
        artifact_payloads_by_sha256=payloads,
    )


def _pop_role_item(graph: dict, payloads: dict[str, dict], role: str, key: str) -> None:
    artifact = next(
        artifact
        for artifact in graph["artifacts"]
        for link in graph["evidence_artifacts"]
        if link["artifact_id"] == artifact["id"] and link["role"] == role
    )
    old_sha = artifact["sha256"]
    payload = payloads.pop(old_sha)
    payload[key].pop()
    new_sha = sha256_json(payload)
    artifact["sha256"] = new_sha
    payloads[new_sha] = payload


def _mutate_role_payload(graph: dict, payloads: dict[str, dict], role: str, mutation) -> None:
    artifact = next(
        artifact
        for artifact in graph["artifacts"]
        for link in graph["evidence_artifacts"]
        if link["artifact_id"] == artifact["id"] and link["role"] == role
    )
    payload = payloads.pop(artifact["sha256"])
    mutation(payload)
    artifact["sha256"] = sha256_json(payload)
    payloads[artifact["sha256"]] = payload


def test_v37_replay_requires_complete_typed_database_object_graph() -> None:
    result = _validate(_fixture())
    assert result["exact_replay"] is True
    assert result["candidate_count"] == 9
    assert result["raw_proposal_occurrence_count"] == 18
    assert result["pose_count"] == 12
    assert result["rosetta_decoy_count"] == 24
    assert result["agent_decision_count"] == 5


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda graph, _payloads: graph["candidates"].clear(),
            "Candidate evidence",
        ),
        (
            lambda graph, _payloads: graph["tool_call_dependencies"].pop(),
            "source lineage",
        ),
        (
            lambda graph, payloads: _pop_role_item(
                graph, payloads, "pose_manifest", "poses"
            ),
            "pose coverage",
        ),
        (
            lambda graph, payloads: _pop_role_item(
                graph, payloads, "decoy_manifest", "decoys"
            ),
            "decoy coverage",
        ),
        (
            lambda graph, _payloads: graph["agent_decisions"].pop(),
            "AgentDecision set",
        ),
        (
            lambda graph, _payloads: graph["lifecycle_events"].remove(
                next(
                    item
                    for item in graph["lifecycle_events"]
                    if item["event_type"] == "v37.stage_stopped"
                )
            ),
            "stop/failure evidence",
        ),
    ],
)
def test_v37_replay_fails_closed_on_missing_evidence(mutation, match: str) -> None:
    manifest, plan, graph, payloads = _fixture()
    mutation(graph, payloads)
    with pytest.raises(ValueError, match=match):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_fails_closed_on_corrupt_object_payload() -> None:
    manifest, plan, graph, payloads = _fixture()
    artifact = next(
        artifact
        for artifact in graph["artifacts"]
        for link in graph["evidence_artifacts"]
        if link["artifact_id"] == artifact["id"]
        and link["role"] == "proposal_occurrences"
    )
    payloads[artifact["sha256"]] = {"corrupt": True}
    with pytest.raises(ValueError, match="missing or corrupt"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_rejects_metric_plugin_ownership_drift() -> None:
    manifest, plan, graph, payloads = _fixture()
    metric_call = next(
        item
        for item in graph["tool_calls"]
        if item["input_json"]["v37_logical_id"].startswith("v37:metric:")
    )
    metric_call["parameters_json"]["v37_plugin_name"] = "wrong-plugin"
    with pytest.raises(ValueError, match="plugin ownership"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_requires_candidate_specific_knowledge_edges() -> None:
    manifest, plan, graph, payloads = _fixture()
    _mutate_role_payload(
        graph,
        payloads,
        "knowledge_evidence",
        lambda payload: payload["adoption_edges"][0]["candidate_ids"].pop(),
    )
    with pytest.raises(ValueError, match="knowledge applicability candidate edges"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_rejects_evaluation_on_noncanonical_call() -> None:
    manifest, plan, graph, payloads = _fixture()
    graph["tool_calls"].append(
        {
            "id": "physical-call",
            "status": "succeeded",
            "tool_name": "unregistered-metric",
            "input_json": {},
            "input_sha256": sha256_json({}),
            "output_sha256": sha256_json({"result": "fixture"}),
            "idempotency_key": sha256_json({"call_id": "physical-call"}),
            "parameters_json": {},
        }
    )
    event_payload = {
        "tool_call_id": "physical-call",
        "idempotency_key": sha256_json({"call_id": "physical-call"}),
        "input_sha256": sha256_json({}),
        "output_sha256": sha256_json({"result": "fixture"}),
    }
    graph["lifecycle_events"].append(
        {
            "event_type": "tool_call.succeeded",
            "payload_json": event_payload,
            "payload_sha256": sha256_json(event_payload),
        }
    )
    graph["evaluations"].append(
        {
            **graph["evaluations"][0],
            "tool_call_id": "physical-call",
            "metric_name": "unregistered_metric",
        }
    )
    with pytest.raises(ValueError, match="noncanonical metric ToolCall"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_fails_closed_on_missing_pepshot_inspection() -> None:
    manifest, plan, graph, payloads = _fixture()
    _pop_role_item(graph, payloads, "pepshot_evidence", "inspections")
    with pytest.raises(ValueError, match="PepShot candidate inspection coverage"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


@pytest.mark.parametrize(
    ("role", "mutation", "match"),
    [
        (
            "attempt_ledger",
            lambda payload: payload["attempts"].clear(),
            "attempt/failure ledger schema",
        ),
        (
            "knowledge_evidence",
            lambda payload: payload["cards"].clear(),
            "knowledge query/trace/card evidence",
        ),
        (
            "shortlist_manifest",
            lambda payload: payload["selection"]["lane_results"][0].update(
                {"rank": 999}
            ),
            "shortlist Pareto/maximin/risk witnesses",
        ),
        (
            "pepshot_evidence",
            lambda payload: payload["inspections"][0].update(
                {"inspection_sha256": "bad"}
            ),
            "PepShot inspection hash chain",
        ),
        (
            "pose_manifest",
            lambda payload: payload["poses"][0].update(
                {"pocket_coverage_fraction": 0.999}
            ),
            "final Pareto/maximin/risk witnesses",
        ),
        (
            "decoy_manifest",
            lambda payload: payload["decoys"][0].update(
                {"candidate_id": "wrong-candidate"}
            ),
            "Rosetta decoy identity",
        ),
        (
            "final_portfolio",
            lambda payload: payload["selection"]["lane_results"][0].update(
                {"rank": 999}
            ),
            "final Pareto/maximin/risk witnesses",
        ),
    ],
)
def test_v37_replay_rejects_semantically_empty_artifacts(
    role: str, mutation, match: str
) -> None:
    manifest, plan, graph, payloads = _fixture()
    _mutate_role_payload(graph, payloads, role, mutation)
    with pytest.raises(ValueError, match=match):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_rejects_duplicate_boltz_seed_even_with_consistent_call_hashes() -> None:
    manifest, plan, graph, payloads = _fixture()
    pose_artifact = next(
        artifact
        for artifact in graph["artifacts"]
        for link in graph["evidence_artifacts"]
        if link["artifact_id"] == artifact["id"] and link["role"] == "pose_manifest"
    )
    pose_payload = payloads.pop(pose_artifact["sha256"])
    candidate_id = pose_payload["poses"][0]["candidate_id"]
    candidate_poses = [
        item for item in pose_payload["poses"] if item["candidate_id"] == candidate_id
    ]
    replacement_seed = candidate_poses[0]["boltz_seed"]
    changed_pose = candidate_poses[1]
    changed_pose["boltz_seed"] = replacement_seed
    boltz_call = next(
        item for item in graph["tool_calls"] if item["id"] == changed_pose["pose_id"]
    )
    boltz_call["random_seed"] = replacement_seed
    boltz_call["input_json"]["seed"] = replacement_seed
    boltz_call["input_sha256"] = sha256_json(boltz_call["input_json"])
    changed_pose["boltz_input_sha256"] = boltz_call["input_sha256"]
    success_event = next(
        item
        for item in graph["lifecycle_events"]
        if item["event_type"] == "tool_call.succeeded"
        and item["payload_json"]["tool_call_id"] == boltz_call["id"]
    )
    success_event["payload_json"]["input_sha256"] = boltz_call["input_sha256"]
    success_event["payload_sha256"] = sha256_json(success_event["payload_json"])
    pose_artifact["sha256"] = sha256_json(pose_payload)
    payloads[pose_artifact["sha256"]] = pose_payload
    with pytest.raises(ValueError, match="Boltz seed multiset"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_rejects_deleted_pepshot_detail_tool_call() -> None:
    manifest, plan, graph, payloads = _fixture()
    detail_call = next(
        item for item in graph["tool_calls"] if item["tool_name"] == "pepshot-inspect"
    )
    graph["tool_calls"].remove(detail_call)
    graph["lifecycle_events"] = [
        item
        for item in graph["lifecycle_events"]
        if item.get("payload_json", {}).get("tool_call_id") != detail_call["id"]
    ]
    with pytest.raises(ValueError, match="PepShot inspection projection or lineage"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_rejects_misbound_rosetta_detail_tool_call() -> None:
    manifest, plan, graph, payloads = _fixture()
    decoy_artifact = next(
        artifact
        for artifact in graph["artifacts"]
        for link in graph["evidence_artifacts"]
        if link["artifact_id"] == artifact["id"] and link["role"] == "decoy_manifest"
    )
    decoy_payload = payloads.pop(decoy_artifact["sha256"])
    decoy = decoy_payload["decoys"][0]
    wrong_call = next(
        item
        for item in graph["tool_calls"]
        if item["tool_name"] == "pyrosetta-flexpepdock-interface-analyzer"
        and item["id"] != decoy["rosetta_tool_call_id"]
    )
    decoy["rosetta_tool_call_id"] = wrong_call["id"]
    decoy["rosetta_call_input_sha256"] = wrong_call["input_sha256"]
    decoy["rosetta_call_output_sha256"] = wrong_call["output_sha256"]
    decoy_artifact["sha256"] = sha256_json(decoy_payload)
    payloads[decoy_artifact["sha256"]] = decoy_payload
    with pytest.raises(ValueError, match="Rosetta decoy identity"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_rejects_tool_success_event_row_drift() -> None:
    manifest, plan, graph, payloads = _fixture()
    event = next(
        item
        for item in graph["lifecycle_events"]
        if item["event_type"] == "tool_call.succeeded"
    )
    event["payload_json"]["output_sha256"] = "f" * 64
    event["payload_sha256"] = sha256_json(event["payload_json"])
    with pytest.raises(ValueError, match="ToolCall row and success event SHA lineage"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_rejects_committed_graph_snapshot_drift() -> None:
    manifest, plan, graph, payloads = _fixture()
    _mutate_role_payload(
        graph,
        payloads,
        "committed_graph_snapshot",
        lambda payload: payload["graph"].update({"drift": True}),
    )
    with pytest.raises(ValueError, match="committed graph artifact hash chain"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_rejects_missing_runtime_receipt_commit_event() -> None:
    manifest, plan, graph, payloads = _fixture()
    event = next(
        item
        for item in graph["lifecycle_events"]
        if item["event_type"] == "v37.runtime_receipts.committed"
    )
    graph["lifecycle_events"].remove(event)
    with pytest.raises(ValueError, match="runtime receipt artifacts and commit events"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_rejects_duplicate_runtime_receipt_commit_event() -> None:
    manifest, plan, graph, payloads = _fixture()
    event = next(
        item
        for item in graph["lifecycle_events"]
        if item["event_type"] == "v37.runtime_receipts.committed"
    )
    graph["lifecycle_events"].append(dict(event))
    with pytest.raises(ValueError, match="runtime receipt artifacts and commit events"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_rejects_runtime_receipt_boundary_drift() -> None:
    manifest, plan, graph, payloads = _fixture()
    event = next(
        item
        for item in graph["lifecycle_events"]
        if item["event_type"] == "v37.runtime_receipts.committed"
    )
    artifact = next(
        item
        for item in graph["artifacts"]
        if item["sha256"] == event["payload_json"]["artifact_sha256"]
    )
    receipt = payloads.pop(artifact["sha256"])
    receipt["completion"]["identity"]["drift"] = True
    receipt["completion"]["byte_identity_sha256"] = sha256_json(
        receipt["completion"]["identity"]
    )
    receipt["completion"]["launch_receipt_sha256"] = sha256_json(
        {
            key: value
            for key, value in receipt["completion"].items()
            if key != "launch_receipt_sha256"
        }
    )
    receipt["launch_receipt_sha256"] = sha256_json(
        {key: value for key, value in receipt.items() if key != "launch_receipt_sha256"}
    )
    new_sha256 = sha256_json(receipt)
    artifact["sha256"] = new_sha256
    payloads[new_sha256] = receipt
    event["payload_json"]["artifact_sha256"] = new_sha256
    event["payload_json"]["launch_receipt_sha256"] = receipt["launch_receipt_sha256"]
    event["payload_sha256"] = sha256_json(event["payload_json"])
    with pytest.raises(ValueError, match="runtime receipt boundaries differ"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def _append_attempt_runtime_evidence(
    graph: dict, payloads: dict[str, dict], *, include_aggregate: bool = True
) -> None:
    aggregate_id = "attempt-aggregate-1"
    identity = {
        "run_id": "fixture-run",
        "v37_logical_id": "v37:generate:hydramp:20270371",
        "activity_name": "generate_v37_batch",
        "attempt": 1,
    }
    aggregate_receipt = _runtime_receipt(identity["v37_logical_id"])
    launch_receipt = aggregate_receipt["pre_snapshot"]
    launch_artifact_sha256 = sha256_json(launch_receipt)
    aggregate_artifact_sha256 = sha256_json(aggregate_receipt)
    for digest, payload in (
        (launch_artifact_sha256, launch_receipt),
        (aggregate_artifact_sha256, aggregate_receipt),
    ):
        graph["artifacts"].append(
            {"id": f"attempt-artifact-{len(graph['artifacts']) + 1}", "sha256": digest}
        )
        payloads[digest] = payload
    rows = [
        ("v37.attempt_started", {**identity, "status": "started"}),
        (
            "v37.launch_receipt_persisted",
            {
                **identity,
                "artifact_sha256": launch_artifact_sha256,
                "launch_receipt_sha256": launch_receipt["launch_receipt_sha256"],
            },
        ),
    ]
    if include_aggregate:
        rows.append(
            (
                "v37.aggregate_launch_receipt_persisted",
                {
                    **identity,
                    "artifact_sha256": aggregate_artifact_sha256,
                    "launch_receipt_sha256": aggregate_receipt[
                        "launch_receipt_sha256"
                    ],
                    "all_boundaries_match": True,
                },
            )
        )
    rows.append(
        (
            "v37.attempt_succeeded",
            {**identity, "status": "succeeded", "output_sha256": "a" * 64},
        )
    )
    for sequence_no, (event_type, event_payload) in enumerate(rows, start=1):
        graph["lifecycle_events"].append(
            {
                "aggregate_type": "v37_attempt",
                "aggregate_id": aggregate_id,
                "sequence_no": sequence_no,
                "event_type": event_type,
                "payload_json": event_payload,
                "payload_sha256": sha256_json(event_payload),
            }
        )


def test_v37_replay_accepts_complete_attempt_runtime_evidence() -> None:
    manifest, plan, graph, payloads = _fixture()
    _append_attempt_runtime_evidence(graph, payloads)
    validate_v37_database_object_replay(
        manifest=manifest,
        plan=plan,
        graph=graph,
        artifact_payloads_by_sha256=payloads,
    )


def test_v37_replay_rejects_succeeded_attempt_without_aggregate_receipt() -> None:
    manifest, plan, graph, payloads = _fixture()
    _append_attempt_runtime_evidence(graph, payloads, include_aggregate=False)
    with pytest.raises(ValueError, match="attempt lifecycle is incomplete"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )
