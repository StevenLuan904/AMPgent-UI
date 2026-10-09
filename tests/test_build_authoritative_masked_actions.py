import csv
import hashlib
import json
from pathlib import Path

import pytest

from analysis.build_authoritative_masked_actions import build_actions
from analysis.generation_call_registration_helper import canonical_action_sha256

TARGETS = {
    "acea": "ACDEFGHIKLMNPQRSTVWYAC",
    "vegfa": "CDEFGHIKLMNPQRSTVWYACD",
}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _plan(
    action_id, parent_uuid, parent_label, parent_sequence, generation, positions, target="acea"
):
    return {
        "action_id": action_id,
        "action_kind": "masked_substitution",
        "target": target,
        "parent_id": parent_label,
        "parent_typed_uuid": parent_uuid,
        "parent_sequence": parent_sequence,
        "primary_parent_sequence": parent_sequence,
        "lineage_generation": generation,
        "parent_lineage_generation": generation - 1,
        "mutation_positions": list(positions),
        "model_revision": "rev-test",
    }


def _case(tmp_path: Path, *, round_number=207, selected_masks=((2, 5), (3, 8))):
    preflight = tmp_path / "preflight.json"
    history = tmp_path / "history.json"
    archive = tmp_path / "baseline.csv"
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "out"
    parent_rows = [
        {
            "auth": "parent-a",
            "own": "own-a",
            "parent": "ancestor-a",
            "parent_uuid": "ancestor-uuid-a",
            "sequence": "ACDEFGHIKLMNPQRSTVWY",
            "parent_sequence": "YWVTSRQPNMLKIHGFEDCA",
            "generation": 3,
            "call": "call-a",
        },
        {
            "auth": "parent-b",
            "own": "own-b",
            "parent": "ancestor-b",
            "parent_uuid": "ancestor-uuid-b",
            "sequence": "CDEFGHIKLMNPQRSTVWYAC",
            "parent_sequence": "ACYWVTSRQPNMLKIHGFED",
            "generation": 5,
            "call": "call-b",
        },
    ]
    preflight.write_text(
        json.dumps(
            {
                "run": {
                    "id": "run-test",
                    "spec": {
                        "root_campaign_id": "campaign-test",
                        "scientific_root_id": "root-test",
                    },
                },
                "candidates": [
                    {
                        "id": row["own"],
                        "sequence": row["sequence"],
                        "generation": row["generation"],
                        "parent_id": row["parent_uuid"],
                        "generator_call_id": row["call"],
                        "metadata": {"authoritative_candidate_id": row["auth"]},
                    }
                    for row in parent_rows
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "targets": [
                    {"target_key": key, "sequence": value, "sequence_sha256": _sha(value)}
                    for key, value in TARGETS.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    fields = [
        "candidate_id",
        "authoritative_candidate_id",
        "sequence",
        "generation",
        "parent_typed_uuid",
        "parent_candidate_id",
        "parent_sequence",
        "action_id",
        "quality",
        "parent_delta_phi",
        "parent_delta_objectives",
        "charge_density",
        "hydrophobicity",
        "moment",
        "length",
    ]
    with archive.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in parent_rows:
            writer.writerow(
                {
                    "candidate_id": row["auth"],
                    "authoritative_candidate_id": row["auth"],
                    "sequence": row["sequence"],
                    "generation": row["generation"],
                    "parent_typed_uuid": row["parent_uuid"],
                    "parent_candidate_id": row["parent"],
                    "parent_sequence": row["parent_sequence"],
                    "action_id": row["auth"],
                    "quality": "-1",
                    "parent_delta_phi": "{}",
                    "parent_delta_objectives": "{}",
                    "charge_density": "0.1",
                    "hydrophobicity": "0.2",
                    "moment": "0.3",
                    "length": "20",
                }
            )
    origin_calls = [
        {
            "id": row["call"],
            "status": "completed",
            "input": {
                "parent_action_plans": [
                    _plan(
                        row["auth"],
                        row["parent_uuid"],
                        row["parent"],
                        row["parent_sequence"],
                        row["generation"],
                        [1],
                    )
                ]
            },
            "parameters": {
                "parent_action_plans": [
                    _plan(
                        row["auth"],
                        row["parent_uuid"],
                        row["parent"],
                        row["parent_sequence"],
                        row["generation"],
                        [1],
                    )
                ]
            },
        }
        for row in parent_rows
    ]
    downstream = [
        {
            "action_id": "sibling-mask",
            "parent_typed_uuid": "sibling-own",
            "mutation_positions": [2, 5],
        },
        {"action_id": "b-mask-1", "parent_typed_uuid": "own-b", "mutation_positions": [7, 9]},
        {"action_id": "b-mask-2", "parent_typed_uuid": "own-b", "mutation_positions": [7, 9]},
    ]
    calls = origin_calls + [
        {
            "id": f"downstream-{i}",
            "status": "completed",
            "input": {"parent_action_plans": [plan]},
            "parameters": {"parent_action_plans": [plan]},
        }
        for i, plan in enumerate(downstream)
    ]
    history.write_text(json.dumps({"successful_generation_calls": calls}), encoding="utf-8")
    selections = [
        {
            "authoritative_candidate_id": "parent-a",
            "target": "acea",
            "mask": list(selected_masks[0]),
            "seed": 100,
        },
        {
            "authoritative_candidate_id": "parent-a",
            "target": "vegfa",
            "mask": list(selected_masks[0]),
            "seed": 101,
        },
        {
            "authoritative_candidate_id": "parent-b",
            "target": "acea",
            "mask": list(selected_masks[1]),
            "seed": 102,
        },
        {
            "authoritative_candidate_id": "parent-b",
            "target": "vegfa",
            "mask": list(selected_masks[1]),
            "seed": 103,
        },
    ]
    return locals()


@pytest.mark.parametrize("round_number,masks", [(207, ((2, 5), (3, 8))), (42, ((4, 11), (6, 12)))])
def test_builder_is_round_and_mask_parameterized(tmp_path, round_number, masks):
    case = _case(tmp_path, round_number=round_number, selected_masks=masks)
    receipt = build_actions(
        preflight_path=case["preflight"],
        generation_readback_path=case["history"],
        archive_path=case["archive"],
        expected_baseline_path=case["archive"],
        output_dir=case["output"],
        selections=case["selections"],
        target_manifest_path=case["manifest"],
        round_number=round_number,
        campaign_id="campaign-test",
        run_id="run-test",
        root_id="root-test",
        model_revision="rev-test",
        remote_generation_root=f"remote/gen/{round_number}",
        remote_source_root=f"remote/src/{round_number}",
    )
    assert receipt["action_count"] == 4
    rows = list(
        csv.DictReader((case["output"] / f"r{round_number}_actions.csv").open(encoding="utf-8"))
    )
    assert [json.loads(row["mutation_positions"]) for row in rows].count(list(masks[0])) == 2
    assert [json.loads(row["mutation_positions"]) for row in rows].count(list(masks[1])) == 2
    context = json.loads(
        (case["output"] / f"r{round_number}_context.json").read_text(encoding="utf-8")
    )
    assert context["parents"][0]["tested_mutation_position_tuples"] == []
    assert context["parents"][1]["tested_mutation_position_tuples"] == [[7, 9]]
    freeze = json.loads(
        (case["output"] / f"r{round_number}_freeze.json").read_text(encoding="utf-8")
    )
    assert freeze["assertions"]["baseline_matches_explicit_expected_input"] is True
    assert freeze["remote_generation_root"] == f"remote/gen/{round_number}"

    for target in ("acea", "vegfa"):
        request_path = case["output"] / f"r{round_number}_{target}_request.json"
        request = json.loads(request_path.read_text(encoding="utf-8"))
        assert hashlib.sha256(request_path.read_bytes()).hexdigest()
        assert request["campaign_id"] == "campaign-test"
        assert request["frozen"]["remote_source_root"] == f"remote/src/{round_number}"
        for action in request["action_plans"]:
            assert action["action_sha256"] == canonical_action_sha256(action)


def test_builder_rejects_baseline_or_paired_target_mismatch(tmp_path):
    case = _case(tmp_path)
    with pytest.raises(ValueError, match="expected_baseline"):
        build_actions(
            preflight_path=case["preflight"],
            generation_readback_path=case["history"],
            archive_path=case["archive"],
            expected_baseline_path=tmp_path / "other.csv",
            output_dir=case["output"],
            selections=case["selections"],
            target_manifest_path=case["manifest"],
            round_number=207,
            campaign_id="campaign-test",
            run_id="run-test",
            root_id="root-test",
            model_revision="rev-test",
        )
    bad = [dict(item) for item in case["selections"]]
    bad[1]["mask"] = [2, 6]
    with pytest.raises(ValueError, match="paired targets"):
        build_actions(
            preflight_path=case["preflight"],
            generation_readback_path=case["history"],
            archive_path=case["archive"],
            expected_baseline_path=case["archive"],
            output_dir=case["output"],
            selections=bad,
            target_manifest_path=case["manifest"],
            round_number=207,
            campaign_id="campaign-test",
            run_id="run-test",
            root_id="root-test",
            model_revision="rev-test",
        )
