"""Build a dynamic typed candidate-evaluation v2 plan from completed score artifacts.

This is model-free and write-free.  It keeps raw occurrences while scoring each
global-sequence canonical only once, and emits the executable top-level arrays
expected by the PG import contract.
"""
import argparse
import csv
import hashlib
import json
import uuid
from pathlib import Path

from analysis.r132_candidate_evaluation_plan import PRIMARY, TEXT, UNITS, PROV
from analysis.r132_occurrence_import_contract import build_occurrence_rows

ROOT = uuid.UUID("f72805f4-7547-5017-a069-74042708d228")
TARGET_UUID = {"acea": "6c45ae22-e578-4b3e-a885-dee1f11b6390", "vegfa": "8dd7eb39-6c4a-4c3e-bbc3-b6add9aecd35"}


def rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stable(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def truth(value):
    return str(value).lower() == "true"


def build(args):
    rd = Path(args.round_dir)
    label = args.round_label
    freeze = read_json(rd / args.freeze)
    run_id = str(freeze.get("run_id", args.run_id))
    campaign = str(freeze.get("campaign_id", args.campaign_id))
    remote = str(freeze.get("remote_source_root", "")).rstrip("/")
    raw = rows(rd / args.raw)
    enriched = rows(rd / args.enriched)
    scores = rows(rd / args.scores)
    amps = rows(rd / args.amps)
    aliases = read_json(rd / args.aliases).get("aliases", [])
    registration = read_json(rd / args.registration)
    generation = read_json(rd / args.generation)
    scorer_regs = registration.get("scorer_registrations", [])
    formal_tc = next(x["id"] for x in scorer_regs if x.get("batch_kind") == "formal12")
    amp_tc = next(x["id"] for x in scorer_regs if x.get("batch_kind") == "amplify")
    gen = {x["target"]: x["id"] for x in generation.get("calls", []) if x.get("target") in TARGET_UUID}
    if set(gen) != set(TARGET_UUID):
        raise RuntimeError("generation registration must contain both AceA and VEGFA calls")

    actions = {}
    for spec in args.action:
        target, path = spec.split("=", 1)
        actions[target] = read_json(rd / path).get("action_plans", [])
    if set(actions) != set(TARGET_UUID):
        raise RuntimeError("action files must cover AceA and VEGFA")

    first = {}
    for alias in aliases:
        first.setdefault(alias["canonical_unique_candidate_id"], alias)
    if not first:
        raise RuntimeError("alias map has no canonical rows")
    raw_sequences = {x["sequence"] for x in raw}
    canonical_sequences = {x["sequence"] for x in first.values()}
    if raw_sequences != canonical_sequences:
        raise RuntimeError("alias map does not cover all global unique sequences")

    alias_by_raw = {x["raw_candidate_id"]: x for x in aliases}
    enriched_by_action = {x["action_id"]: x for x in enriched}
    score_by_id = {x["candidate_id"]: x for x in scores}
    amp_by_sequence = {x["sequence"]: x for x in amps}
    candidate_id_by_sequence = {
        alias["sequence"]: str(uuid.uuid5(ROOT, f"{campaign}:{label}:candidate:{canonical}") )
        for canonical, alias in first.items()
    }
    generated = {
        target: [
            {k: (float(x[k]) if k in {"conditional_nll", "conditional_ppl"} else x[k])
             for k in ("action_id", "sequence", "conditional_nll", "conditional_ppl")}
            for x in raw if x["target"] == target
        ] for target in TARGET_UUID
    }
    occurrences = build_occurrence_rows(
        run_id=run_id, target_tool_calls=gen, action_plans=actions,
        generated_rows=generated, canonical_candidate_by_sequence=candidate_id_by_sequence,
        root_uuid=str(ROOT),
    )
    import_id = str(uuid.uuid5(ROOT, f"{campaign}:{label}:score-import.v2"))
    candidates, evaluations = [], []
    for canonical, alias in first.items():
        row = enriched_by_action[canonical]
        cid = candidate_id_by_sequence[row["sequence"]]
        score = score_by_id[row["source_scorer_candidate_id"]]
        amp = amp_by_sequence[row["sequence"]]
        admission = row.get("archive_status") == "eligible"
        for metric in PRIMARY:
            plugin, release = PROV[metric]
            evaluations.append({
                "id": str(uuid.uuid5(ROOT, f"{campaign}:{label}:evaluation:{canonical}:{metric}")),
                "candidate_id": cid, "subject_run_id": run_id, "tool_call_id": import_id,
                "authoritative_candidate_id": canonical, "metric_name": metric,
                "numeric_value": None if metric in TEXT else float(score[metric]),
                "text_value": score[metric] if metric in TEXT else None, "unit": UNITS[metric],
                "status": "succeeded", "scorer_tool_call_id": formal_tc, "evidence_role": "primary", "evidence_family": "score_all",
                "model_release_key": release, "out_of_domain": False, "ood_status": "unknown_not_assessed",
                "limitations": ["OOD not independently assessed"],
                "raw_source": {"raw_candidate_id": score["candidate_id"], "scorer_tool_call_id": formal_tc,
                               "provider_plugin": plugin, "provider_contract": release,
                               "metric_artifact": str(rd / "score_all_r116_runner_local_unique" / "metrics" / f"{plugin}.json"),
                               "local_score_dir": str(rd / "score_all_r116_runner_local_unique"), "remote_root": remote},
                "candidate_admission_not_implied": not admission,
            })
        for model, role, family in (("HemoPI2", "challenger", "hemolysis"), ("APEX", "shadow", "activity"), ("PeptiVerse", "shadow", "activity")):
            evaluations.append({
                "id": str(uuid.uuid5(ROOT, f"{campaign}:{label}:evaluation:{canonical}:{model}")),
                "candidate_id": cid, "subject_run_id": run_id, "tool_call_id": import_id,
                "authoritative_candidate_id": canonical, "metric_name": f"{model}_availability",
                "numeric_value": None, "text_value": None, "unit": None, "status": "unsupported",
                "evidence_role": role, "evidence_family": family,
                "model_release_key": f"inventory:{model.lower()}:runtime_unavailable:release_unknown",
                "applicability_status": "runtime_unavailable", "conflict_status": "not_assessed",
                "out_of_domain": False, "ood_status": "unknown_not_assessed",
                "limitations": ["runtime unavailable; not executed scoring"],
                "raw_source": {"metric_status": str(rd / "score_all_r116_runner_local_unique" / "metric_status.csv"), "remote_root": remote},
                "candidate_admission_not_implied": not admission,
            })
        evaluations.append({
            "id": str(uuid.uuid5(ROOT, f"{campaign}:{label}:evaluation:{canonical}:AMPlify")),
            "candidate_id": cid, "subject_run_id": run_id, "tool_call_id": import_id,
            "authoritative_candidate_id": canonical, "metric_name": "amplify_probability",
            "numeric_value": float(amp["amplify_probability"]), "text_value": amp["amplify_label"],
            "unit": "probability", "status": "succeeded", "scorer_tool_call_id": amp_tc, "evidence_role": "shadow",
            "evidence_family": "amp_likelihood", "model_release_key": "amplify-2.0.1-py36hdfd78af_2",
            "out_of_domain": False, "ood_status": "unknown_not_assessed",
            "limitations": ["soft AMP likelihood only; OOD not assessed"],
            "raw_source": {"amplify_csv": str(rd / args.amps), "scorer_tool_call_id": amp_tc,
                           "five_submodel_probabilities": {f"submodel_{i}": amp[f"amplify_submodel_{i}_probability"] for i in range(1, 6)},
                           "remote_root": remote}, "candidate_admission_not_implied": not admission,
        })
        candidates.append({
            "id": cid, "authoritative_candidate_id": canonical, "source_scorer_candidate_id": score["candidate_id"],
            "run_id": run_id, "sequence": row["sequence"], "sequence_sha256": hashlib.sha256(row["sequence"].encode()).hexdigest(),
            "generation": int(row["generation"]), "parent_id": row["parent_typed_uuid"], "parent_sequence": row["parent_sequence"],
            "parent_generation": int(row["parent_generation"]), "generator_tool_call_id": gen[row["target"]],
            "target_role": row["target"], "target_uuid": TARGET_UUID[row["target"]],
            "dual_support_min": int(row["dual_reference_support_min"]), "support_acea": int(row["support_acea"]),
            "support_vegfa": int(row["support_vegfa"]), "display_eligible": truth(row["display_hard_gate"]),
            "candidate_admission_eligible": admission,
            "raw_occurrence_ids": [x["id"] for x in occurrences if x["candidate_id"] == cid],
        })

    source_artifacts = {
        "raw_occurrences": str(rd / args.raw), "alias_map": str(rd / args.aliases),
        "enriched": str(rd / args.enriched), "formal_scores": str(rd / args.scores),
        "amplify_scores": str(rd / args.amps), "calibration": str(rd / args.calibration),
        "prior_archive": args.prior_archive, "prior_B": args.prior_B, "remote_root": remote,
    }
    inp = {"campaign_id": campaign, "run_id": run_id, "round": label, "source_artifacts": source_artifacts,
           "counts_as_scorer_invocation": False, "candidate_authoritative_ids": [x["authoritative_candidate_id"] for x in candidates],
           "raw_occurrence_count": len(raw), "canonical_unique_count": len(first),
           "dual_calibration_witness_rows": 2 * len(first), "formal_tool_call_id": formal_tc, "amplify_tool_call_id": amp_tc}
    plan = {
        "schema_version": f"ampgent.{label}.candidate-evaluation-plan.v2", "mode": "root_review_required_no_write",
        "plan_status": "complete_scores_pending_root_import", "campaign_id": campaign, "run_id": run_id, "round": label,
        "scientific_root_id": str(ROOT), "evaluations_per_candidate": 16,
        "score_import_tool_call": {"id": import_id, "tool_name": f"ampgent.import_{label}_score_evidence",
                                    "counts_as_scorer_invocation": False,
                                    "idempotency_key": stable({"operation": f"{run_id}:{label}:score-import.v2"}),
                                    "input_json": inp, "input_sha256": stable(inp)},
        "source_artifacts": source_artifacts, "candidates": candidates, "evaluations": evaluations,
        "candidate_occurrences": occurrences,
        "planned_inserts": {"tool_calls": 1, "candidates": len(candidates), "evaluations": len(evaluations), "candidate_occurrences": len(occurrences)},
        "scorer_call_bindings": [
            {"tool_call_id": formal_tc, "batch_kind": "formal12", "candidate_ids": [x["authoritative_candidate_id"] for x in candidates], "input_sha256": registration.get("input_sha256")},
            {"tool_call_id": amp_tc, "batch_kind": "amplify", "candidate_ids": [x["authoritative_candidate_id"] for x in candidates], "input_sha256": registration.get("input_sha256")}
        ],
        "transaction_guards": {"run_id": run_id, "generator_calls": list(gen.values()), "raw_occurrences_preserved": True,
                                "parent_same_run_sequence_generation": True, "no_write_until_root_approval": True,
                                "occurrence_retry_key": ["tool_call_id", "occurrence_rank"]},
        "verification": {"unique_candidate_ids": len({x["id"] for x in candidates}) == len(candidates),
                          "evaluation_count": len(evaluations) == 16 * len(candidates),
                          "evaluations_per_candidate": {x["id"]: sum(y["candidate_id"] == x["id"] for y in evaluations) for x in candidates},
                          "occurrence_count": len(occurrences) == len(raw),
                          "occurrences_per_target": {t: sum(x["opaque_arm_label"] == t for x in occurrences) for t in TARGET_UUID},
                          "global_unique_sequence_count": len(raw_sequences) == len(first), "no_model_or_pg_write": True},
        "policy": {"formal12_plus_amp_soft_plus_3_unavailable_per_unique": True, "amp_soft_not_gate": True,
                   "support_admission_threshold": 2, "historical_archive_immutable": True, "ood": "unknown_not_assessed"},
    }
    output = Path(args.output)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output), "sha256": file_sha(output), "candidates": len(candidates), "evaluations": len(evaluations), "occurrences": len(occurrences), "import_id": import_id}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--round-dir", required=True)
    parser.add_argument("--round-label", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--freeze", default="r143_freeze.json")
    parser.add_argument("--raw", required=True)
    parser.add_argument("--enriched", required=True)
    parser.add_argument("--scores", required=True)
    parser.add_argument("--amps", required=True)
    parser.add_argument("--aliases", required=True)
    parser.add_argument("--registration", required=True)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--action", action="append", required=True, help="target=relative_request_json")
    parser.add_argument("--calibration", required=True)
    parser.add_argument("--prior-archive", required=True)
    parser.add_argument("--prior-B", required=True)
    parser.add_argument("--run-id", default="61d65749-dab0-55db-b62a-6834e4fe604d")
    parser.add_argument("--campaign-id", default="acea-vegfa-dual-autoresearch-20260923-v1")
    print(json.dumps(build(parser.parse_args()), ensure_ascii=False))
