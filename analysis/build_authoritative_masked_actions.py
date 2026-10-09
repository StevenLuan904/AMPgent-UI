"""Build machine-auditable masked actions without DB/model side effects."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from analysis.generation_call_registration_helper import (
    TARGET_UUID,
    canonical_action_sha256,
    resolve_self_identity,
)

DEFAULT_REMOTE_CAMPAIGN_ROOT = "/data1/huangyueshan/pepagent/runs"


def _action_plans(call: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the complete persisted action property before parsing actions."""
    properties: list[Any] = []
    for slot in ("input", "parameters"):
        value = call.get(slot)
        if isinstance(value, dict) and "parent_action_plans" in value:
            properties.append(value["parent_action_plans"])
    if not properties:
        return []
    if len(properties) == 2 and properties[0] != properties[1]:
        raise ValueError(f"input/parameters action arrays disagree for call {call.get('id')}")
    plans = properties[0]
    if not isinstance(plans, list) or any(not isinstance(plan, dict) for plan in plans):
        raise ValueError(f"malformed parent_action_plans for call {call.get('id')}")
    return plans


def _materialize_generation_call(call: dict[str, Any]) -> dict[str, Any]:
    """Accept PG readback calls whose input/parameters are JSON strings."""
    materialized = dict(call)
    for field in ("input", "parameters"):
        if field not in materialized and f"{field}_json" in materialized:
            raw = materialized[f"{field}_json"]
            if isinstance(raw, str):
                materialized[field] = json.loads(raw)
            elif isinstance(raw, dict):
                materialized[field] = raw
    return materialized


def _positions(plan: dict[str, Any], *, source: str) -> tuple[int, ...]:
    values = plan.get("mutation_positions")
    if not isinstance(values, list) or not values:
        raise ValueError(f"{source} action has no complete mutation_positions tuple")
    try:
        positions = tuple(int(value) for value in values)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{source} action has non-integer mutation positions") from error
    if len(set(positions)) != len(positions) or tuple(sorted(positions)) != positions:
        raise ValueError(f"{source} action mutation_positions must be sorted and unique")
    return positions


def _normalize_selected_mask(selection: dict[str, Any]) -> list[int]:
    raw = selection.get("masks", selection.get("mask"))
    if not isinstance(raw, list):
        raw = [raw]
    if not raw:
        raise ValueError("masked substitution requires at least one position")
    if any(isinstance(value, bool) for value in raw):
        raise ValueError(f"selection has no mask array: {selection!r}")
    try:
        positions = [int(value) for value in raw]
    except (TypeError, ValueError) as error:
        raise ValueError(f"selection mask is not integer-valued: {selection!r}") from error
    if not positions:
        raise ValueError("masked substitution requires at least one position")
    if len(set(positions)) != len(positions) or positions != sorted(positions):
        raise ValueError("selection masks must be sorted and unique")
    return positions


def _read_archive(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"archive is empty: {path}")
    return rows


def _archive_authoritative_id(row: dict[str, str]) -> str | None:
    """Resolve the archive identity without rewriting the source archive.

    Older append-only exports carry the authoritative label in ``candidate_id``
    and leave the newer alias column blank.  This fallback is only an in-memory
    builder adapter; the original archive remains the successor baseline.
    """
    # Append-only archives can carry a duplicated lineage alias while the
    # occurrence-level candidate_id remains the unique target-specific key.
    # Prefer that concrete key when present; retain the alias as provenance.
    return row.get("candidate_id") or row.get("authoritative_candidate_id") or None


def _parent_delta_phi_semantics(row: dict[str, str]) -> str:
    raw = row.get("parent_delta_phi")
    if not raw:
        return "unavailable_missing_qd_axes"
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return "unavailable_invalid_qd_axes"
    if isinstance(value, dict) and {
        "charge_density", "hydrophobicity", "moment", "length"
    }.issubset(value):
        return "qd_axes_charge_density_hydrophobicity_moment_length"
    return "unavailable_missing_qd_axes"


def _origin_action(
    *, identity: dict[str, Any], call: dict[str, Any], archive_row: dict[str, str]
) -> dict[str, Any]:
    matches = [
        plan
        for plan in _action_plans(call)
        if plan.get("action_id") in {
            _archive_authoritative_id(archive_row),
            archive_row.get("authoritative_candidate_id"),
        }
    ]
    if len(matches) != 1:
        raise ValueError(
            "self origin action does not uniquely match generator_call_id: "
            f"{archive_row.get('authoritative_candidate_id')}"
        )
    plan = matches[0]
    if plan.get("parent_typed_uuid") != identity["parent_id"]:
        raise ValueError("self origin parent_typed_uuid does not match preflight parent_id")
    # Historical action ``parent_id`` is the authoritative archive label;
    # typed identity is carried by ``parent_typed_uuid``.  Check both against
    # their respective machine-resolved sources rather than conflating them.
    if plan.get("parent_id") not in (
        None,
        archive_row.get("parent_candidate_id"),
        identity.get("parent_id"),
    ):
        raise ValueError("self origin parent_id does not match archive parent label")
    if plan.get("parent_sequence") != archive_row.get("parent_sequence"):
        raise ValueError("self origin parent sequence does not match archive")
    if plan.get("lineage_generation") != int(identity["generation"]):
        raise ValueError("self origin lineage generation does not match preflight")
    if plan.get("parent_lineage_generation") != int(identity["generation"]) - 1:
        raise ValueError("self origin parent generation does not match preflight")
    if plan.get("primary_parent_sequence") != archive_row.get("parent_sequence"):
        raise ValueError("self origin primary parent sequence does not match archive")
    _positions(plan, source="self origin")
    return plan


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _request(
    *,
    target: str,
    target_row: dict[str, Any],
    actions: list[dict[str, Any]],
    round_number: int,
    target_manifest_path: Path,
    campaign_id: str,
    run_id: str,
    model_revision: str,
    remote_generation_root: str,
    remote_source_root: str,
    source_actions_csv: str,
) -> dict[str, Any]:
    sequence = target_row["sequence"]
    return {
        "schema_version": "ampgent.pepmlm.autoresearch-actions.v1",
        "action_kind": "masked_substitution",
        "top_k": 12,
        "temperature": 0.8,
        "model": f"/data1/huangyueshan/pepagent/models/PepMLM-650M/{model_revision}",
        "revision": model_revision,
        "full_targets": True,
        "protocol": "formal12_plus_AMPlify_dynamic_shadow_QD_lineage",
        "target_sequence": sequence,
        "target_identity": {
            "sequence_length": len(sequence),
            "sequence_sha256": target_row["sequence_sha256"],
            "target_key": target,
            "target_sequence_source": str(target_manifest_path),
        },
        "operational_run_id": run_id,
        "proposal_round": round_number,
        "lineage_generation": "parent_generation_plus_one",
        "parent_lineage_generation": "machine_resolved_from_preflight",
        "operator": f"PepMLM target-conditioned masked substitution r{round_number}",
        "operator_identity": f"pepmlm_targeted.masked_substitution.r{round_number}.v1",
        "campaign_id": campaign_id,
        "target_key": target,
        "seed": min(int(action["seed"]) for action in actions),
        "action_plans": actions,
        "frozen": {
            "top_k": 12,
            "temperature": 0.8,
            "no_expected_residue_field": True,
            "no_rosetta_or_md": True,
            "no_gpu_or_md": True,
            "operational_run_id": run_id,
            "source_actions_csv": source_actions_csv,
            "remote_generation_root": remote_generation_root,
            "remote_source_root": remote_source_root,
        },
    }


def build_actions(
    *,
    preflight_path: Path,
    generation_readback_path: Path,
    archive_path: Path,
    expected_baseline_path: Path,
    output_dir: Path,
    selections: list[dict[str, Any]],
    target_manifest_path: Path,
    round_number: int,
    runtime_context_path: Path | None = None,
    supplemental_generation_readback_path: Path | None = None,
    campaign_id: str | None = None,
    run_id: str | None = None,
    root_id: str | None = None,
    model_revision: str | None = None,
    remote_campaign_root: str = DEFAULT_REMOTE_CAMPAIGN_ROOT,
    remote_generation_root: str | None = None,
    remote_source_root: str | None = None,
) -> dict[str, Any]:
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    readback = json.loads(generation_readback_path.read_text(encoding="utf-8"))
    supplemental_readback = (
        json.loads(supplemental_generation_readback_path.read_text(encoding="utf-8"))
        if supplemental_generation_readback_path is not None
        else {}
    )
    manifest = json.loads(target_manifest_path.read_text(encoding="utf-8"))
    archive = _read_archive(archive_path)
    if archive_path.resolve() != expected_baseline_path.resolve():
        raise ValueError("archive_path must equal the explicit expected_baseline_path")
    preflight_run = preflight.get("run", {})
    preflight_spec = preflight_run.get("spec", {})
    campaign_id = campaign_id or preflight_spec.get("root_campaign_id")
    run_id = run_id or preflight_run.get("id")
    root_id = root_id or preflight_spec.get("scientific_root_id")
    if not all(isinstance(value, str) and value for value in (campaign_id, run_id, root_id)):
        raise ValueError(
            "campaign_id, run_id and root_id must be explicit or resolvable from preflight"
        )
    runtime = None
    if runtime_context_path is not None:
        runtime = json.loads(runtime_context_path.read_text(encoding="utf-8"))
        model_revision = model_revision or runtime.get("generation_runtime", {}).get(
            "model_revision"
        )
    if not isinstance(model_revision, str) or not model_revision:
        raise ValueError("model_revision must be explicit or present in runtime_context")
    campaign_root = f"{remote_campaign_root.rstrip('/')}/{campaign_id}"
    remote_generation_root = (
        remote_generation_root
        or f"{campaign_root}/lineage2-proposal_round{round_number}-cpu-validated"
    )
    remote_source_root = (
        remote_source_root
        or (
            f"{campaign_root}/postprocess_round{round_number}_qd/"
            f"r{round_number}_evidence_sources_final"
        )
    )
    target_manifest = {row["target_key"]: row for row in manifest["targets"]}
    rows_by_id = {
        key: row
        for row in archive
        if (key := _archive_authoritative_id(row)) is not None
    }
    rows_by_authoritative_target = {
        (row.get("authoritative_candidate_id"), row.get("target")): row
        for row in archive
        if row.get("authoritative_candidate_id") and row.get("target")
    }
    generation_calls = readback.get("successful_generation_calls")
    if generation_calls is None:
        generation_calls = [
            call for call in readback.get("calls", []) if call.get("status") == "completed"
        ]
    if not generation_calls:
        # PG parent-history preflights expose the same completed call records
        # under this explicit key; preserve the authoritative machine readback
        # instead of reconstructing calls from action labels.
        generation_calls = [
            call
            for call in readback.get("own_parent_generation_calls", [])
            if call.get("status") == "completed"
        ]
    # A parent-preflight may carry the latest completed parent calls while the
    # broader generation ledger carries older calls.  Merge the machine records
    # by id so both sources can resolve the frozen parents without hand-built
    # identity substitutions.
    supplemental_calls = [
        call
        for call in (
            list(preflight.get("own_parent_generation_calls", []))
            + list(supplemental_readback.get("own_parent_generation_calls", []))
        )
        if call.get("status") == "completed"
    ]
    merged_calls = {call.get("id"): call for call in generation_calls if call.get("id")}
    for supplemental in supplemental_calls:
        call_id = supplemental.get("id")
        if not call_id:
            continue
        existing = merged_calls.get(call_id)
        if existing is not None and existing != supplemental:
            raise ValueError(f"supplemental generation payload conflicts for call id: {call_id}")
        merged_calls[call_id] = supplemental
    generation_calls = list(merged_calls.values())
    calls = {
        materialized["id"]: materialized
        for call in generation_calls
        if call.get("status") == "completed"
        for materialized in [_materialize_generation_call(call)]
    }
    if not calls:
        raise ValueError("generation history has no successful completed calls")

    selected_by_parent: dict[str, dict[str, tuple[int, ...]]] = {}
    for selection in selections:
        parent_id = selection.get("authoritative_candidate_id")
        target = selection.get("target")
        selected_tuple = tuple(_normalize_selected_mask(selection))
        by_target = selected_by_parent.setdefault(parent_id, {})
        if target in by_target:
            raise ValueError(f"duplicate target selection for parent: {parent_id}/{target}")
        if by_target and selected_tuple not in by_target.values():
            raise ValueError(f"paired targets must use the same complete mask array: {parent_id}")
        by_target[target] = selected_tuple

    actions: list[dict[str, Any]] = []
    parent_records: dict[str, dict[str, Any]] = {}
    for selection in selections:
        authoritative_id = selection.get("authoritative_candidate_id")
        target = selection.get("target")
        if target not in target_manifest or target not in TARGET_UUID:
            raise ValueError(f"unsupported target in selection: {target!r}")
        archive_row = rows_by_id.get(authoritative_id)
        if archive_row is None or archive_row.get("target") != target:
            archive_row = rows_by_authoritative_target.get((authoritative_id, target))
        if archive_row is None:
            # Some append-only archives retain one authoritative parent row
            # (often AceA) while the paired target reuses the same sequence.
            # Resolve that target from exact sequence/generation/typed-parent
            # identity; never synthesize a target-specific lineage label.
            identity_hint = resolve_self_identity(preflight, authoritative_id)
            candidates = [
                row
                for row in archive
                if row.get("sequence") == identity_hint["sequence"]
                and int(row.get("generation", -1)) == int(identity_hint["generation"])
                and row.get("parent_typed_uuid") == identity_hint["parent_id"]
            ]
            if len(candidates) == 1:
                archive_row = candidates[0]
        if archive_row is None:
            raise ValueError(f"archive row missing authoritative id: {authoritative_id}")
        identity = resolve_self_identity(preflight, authoritative_id)
        if archive_row.get("authoritative_candidate_id") not in (None, "", authoritative_id):
            raise ValueError("archive authoritative id does not match selection")
        if archive_row.get("sequence") != identity["sequence"]:
            raise ValueError("archive sequence does not match preflight self identity")
        if int(archive_row.get("generation", -1)) != int(identity["generation"]):
            raise ValueError("archive generation does not match preflight self identity")
        if archive_row.get("parent_typed_uuid") != identity["parent_id"]:
            raise ValueError("archive parent_typed_uuid does not match preflight parent_id")
        call = calls.get(identity["generator_call_id"])
        if call is None:
            raise ValueError(f"successful generator call missing: {identity['generator_call_id']}")
        origin = _origin_action(identity=identity, call=call, archive_row=archive_row)

        downstream: list[dict[str, Any]] = []
        for completed_call in calls.values():
            for plan in _action_plans(completed_call):
                if plan.get("parent_typed_uuid") == identity["candidate_id"]:
                    _positions(plan, source="downstream")
                    downstream.append(plan)
        tested_tuples = sorted({_positions(plan, source="downstream") for plan in downstream})
        selected_mask = _normalize_selected_mask(selection)
        selected_tuple = tuple(selected_mask)
        if selected_tuple in tested_tuples:
            raise ValueError(
                f"selected complete mask already tested for {authoritative_id}: {selected_tuple}"
            )
        seed = int(selection["seed"])
        target_row = target_manifest[target]
        action_base: dict[str, Any] = {
            "action_id": (
                f"r{round_number}-{target}-{authoritative_id}-"
                f"mask{'-'.join(map(str, selected_mask))}-seed{seed}"
            ),
            "action_kind": "masked_substitution",
            "target": target,
            "proposal_round": round_number,
            "parent_typed_uuid": identity["candidate_id"],
            "parent_id": identity["candidate_id"],
            "primary_parent_id": identity["candidate_id"],
            "parent_sequence": identity["sequence"],
            "primary_parent_sequence": identity["sequence"],
            "parent_lineage_generation": int(identity["generation"]),
            "lineage_generation": int(identity["generation"]) + 1,
            "mutation_positions": selected_mask,
            "action_seed": seed,
            "seed": seed,
            "rationale": (
                f"frozen r{round_number} masked action; model chooses canonical "
                "residues; no replacement specified"
            ),
            "target_sequence": target_row["sequence"],
            "top_k": 12,
            "temperature": 0.8,
            "model_revision": model_revision,
            "operator_identity": f"pepmlm_targeted.masked_substitution.r{round_number}.v1",
            "target_uuid": TARGET_UUID[target],
            "target_sequence_sha256": target_row["sequence_sha256"],
            "parent_authoritative_candidate_id": authoritative_id,
            "parent_generator_call_id": identity["generator_call_id"],
            "archive_candidate_id": archive_row["candidate_id"],
            "archive_action_id": archive_row["action_id"],
            "archive_quality": archive_row.get("quality"),
            "archive_parent_delta_phi": archive_row.get("parent_delta_phi"),
            "archive_parent_delta_phi_semantics": _parent_delta_phi_semantics(archive_row),
            "archive_parent_delta_objectives": archive_row.get("parent_delta_objectives"),
            "parent_qd_axes": {
                "charge_density": archive_row.get("charge_density"),
                "hydrophobicity": archive_row.get("hydrophobicity"),
                "moment": archive_row.get("moment"),
                "length": archive_row.get("length"),
            },
            "archive_sequence": archive_row["sequence"],
            "archive_generation": int(archive_row["generation"]),
            "tested_parent_action_ids": [plan["action_id"] for plan in downstream],
            "tested_masks": [list(mask) for mask in tested_tuples],
        }
        action = dict(action_base)
        action["action_sha256"] = canonical_action_sha256(action_base)
        actions.append(action)
        parent_records[authoritative_id] = {
            "identity": identity,
            "archive": archive_row,
            "origin_action": origin,
            "tested_parent_actions": downstream,
            "tested_mutation_position_tuples": [list(mask) for mask in tested_tuples],
            "selected_mask_array": selected_mask,
        }

    if len({action["action_id"] for action in actions}) != len(actions):
        raise ValueError("actions must have unique action_id values")

    output_dir.mkdir(parents=True, exist_ok=True)
    actions_path = output_dir / f"r{round_number}_actions.csv"
    with actions_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(actions[0]))
        writer.writeheader()
        writer.writerows(
            {key: _csv_value(value) for key, value in action.items()} for action in actions
        )

    baseline_path = archive_path
    source_artifacts: dict[str, Any] = {
        "startup_preflight": str(preflight_path),
        "generation_history": str(generation_readback_path),
        "successor_baseline_archive": str(baseline_path),
        "expected_baseline": str(expected_baseline_path),
        "target_manifest": str(target_manifest_path),
    }
    if supplemental_generation_readback_path is not None:
        source_artifacts["supplemental_generation_readback"] = str(
            supplemental_generation_readback_path
        )
    if runtime_context_path is not None:
        source_artifacts.update(
            {
                "runtime_context": str(runtime_context_path),
                "scoring_registry": runtime["scoring_runtime"]["registry_path"],
                "scoring_registry_sha256": runtime["scoring_runtime"]["registry_sha256"],
                "generation_worker": runtime["generation_runtime"]["worker"],
                "generation_worker_sha256": runtime["generation_runtime"]["worker_sha256"],
            }
        )
    local_worker = Path("worker/tmp/pepmlm_cli_cpu_smoke.py")
    if local_worker.exists():
        source_artifacts["generation_worker_local"] = str(local_worker)
        source_artifacts["generation_worker_local_sha256"] = hashlib.sha256(
            local_worker.read_bytes()
        ).hexdigest()

    assertions = {
        "authoritative_rows_resolved_from_preflight": True,
        "self_origin_generator_call_and_parent_checked": True,
        "archive_sequence_generation_parent_checked": True,
        "tested_parent_action_plans_exact_parent_uuid_and_full_tuple": True,
        "zero_downstream_is_legal": True,
        "sibling_history_not_borrowed": True,
        "selected_actions_are_joint_masks": all(
            len(action["mutation_positions"]) > 1 for action in actions
        ),
        "selected_actions_are_nonempty_masks": all(
            len(action["mutation_positions"]) >= 1 for action in actions
        ),
        "baseline_matches_explicit_expected_input": baseline_path.resolve()
        == expected_baseline_path.resolve(),
        "canonical_worker_action_sha_after_payload_assembly": True,
        "no_generation_registration_yet": True,
        "no_model_launch_yet": True,
    }
    freeze = {
        "schema": "lineage2_masked_substitution_freeze_v2",
        "status": "freeze_review_pending_no_launch",
        "proposal_round": round_number,
        "top_k": 12,
        "temperature": 0.8,
        "model_revision": model_revision,
        "replace_amino_acid": False,
        "run_id": run_id,
        "root_id": root_id,
        "campaign_id": campaign_id,
        "remote_generation_root": remote_generation_root,
        "remote_source_root": remote_source_root,
        "source_artifacts": source_artifacts,
        "actions": actions,
        "resolved_parent_evidence": list(parent_records.values()),
        "assertions": assertions,
    }
    freeze_path = output_dir / f"r{round_number}_freeze.json"
    freeze_path.write_text(
        json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    target_order = list(dict.fromkeys(action["target"] for action in actions))
    context = {
        "schema": "lineage2.masked_actions.context.v2",
        "status": "freeze_review_pending_no_launch",
        "run_id": run_id,
        "root_id": root_id,
        "campaign_id": campaign_id,
        "proposal_round": round_number,
        "remote_generation_root": remote_generation_root,
        "remote_source_root": remote_source_root,
        "model": {
            "top_k": 12,
            "temperature": 0.8,
            "revision": model_revision,
            "replace_amino_acid": False,
        },
        "parents": [
            {
                "authoritative_candidate_id": key,
                "candidate_id": record["identity"]["candidate_id"],
                "sequence": record["identity"]["sequence"],
                "generation": record["identity"]["generation"],
                "parent_id": record["identity"]["parent_id"],
                "generator_call_id": record["identity"]["generator_call_id"],
                "tested_mutation_position_tuples": record["tested_mutation_position_tuples"],
                "selected_mask_array": record["selected_mask_array"],
                "selected_action_ids": [
                    action["action_id"]
                    for action in actions
                    if action["parent_authoritative_candidate_id"] == key
                ],
            }
            for key, record in parent_records.items()
        ],
        "actions": {
            "count": len(actions),
            "target_order": target_order,
            "seed_order": [action["seed"] for action in actions],
            "selected_mask_arrays": [action["mutation_positions"] for action in actions],
            "selection_reason": (
                "paired-target masked exploration; no amino-acid replacement "
                "is pre-specified"
            ),
        },
        "source_artifacts": source_artifacts,
        "guards": assertions,
    }
    context_path = output_dir / f"r{round_number}_context.json"
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    requests: dict[str, str] = {}
    for target in target_order:
        request = _request(
            target=target,
            target_row=target_manifest[target],
            actions=[action for action in actions if action["target"] == target],
            round_number=round_number,
            target_manifest_path=target_manifest_path,
            campaign_id=campaign_id,
            run_id=run_id,
            model_revision=model_revision,
            remote_generation_root=remote_generation_root,
            remote_source_root=remote_source_root,
            source_actions_csv=str(actions_path),
        )
        request_path = output_dir / f"r{round_number}_{target}_request.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        requests[target] = str(request_path)

    selections_path = output_dir / f"r{round_number}_selections.json"
    selections_path.write_text(
        json.dumps(selections, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    receipt = {
        "status": "built_and_asserted_no_registration_no_launch",
        "actions_csv": str(actions_path),
        "freeze_json": str(freeze_path),
        "context_json": str(context_path),
        "selections_json": str(selections_path),
        "requests": requests,
        "action_count": len(actions),
        "selected_authoritative_ids": [
            selection["authoritative_candidate_id"] for selection in selections
        ],
        "action_sha256s": [action["action_sha256"] for action in actions],
        "assertions": assertions,
    }
    receipt_path = output_dir / f"r{round_number}_action_build_receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--generation-readback", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-baseline", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--selections", type=Path, required=True)
    parser.add_argument("--runtime-context", type=Path)
    parser.add_argument("--supplemental-generation-readback", type=Path)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--campaign-id")
    parser.add_argument("--run-id")
    parser.add_argument("--root-id")
    parser.add_argument("--model-revision")
    parser.add_argument("--remote-campaign-root", default=DEFAULT_REMOTE_CAMPAIGN_ROOT)
    parser.add_argument("--remote-generation-root")
    parser.add_argument("--remote-source-root")
    args = parser.parse_args()
    selections = json.loads(args.selections.read_text(encoding="utf-8"))
    print(
        json.dumps(
            build_actions(
                preflight_path=args.preflight,
                generation_readback_path=args.generation_readback,
                archive_path=args.archive,
                expected_baseline_path=args.expected_baseline,
                output_dir=args.output_dir,
                selections=selections,
                target_manifest_path=args.target_manifest,
                round_number=args.round,
                runtime_context_path=args.runtime_context,
                supplemental_generation_readback_path=args.supplemental_generation_readback,
                campaign_id=args.campaign_id,
                run_id=args.run_id,
                root_id=args.root_id,
                model_revision=args.model_revision,
                remote_campaign_root=args.remote_campaign_root,
                remote_generation_root=args.remote_generation_root,
                remote_source_root=args.remote_source_root,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
