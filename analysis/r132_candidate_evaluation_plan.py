"""Build the r132 score/evidence import plan."""

# ruff: noqa: E501

import argparse
import csv
import hashlib
import json
import uuid
from pathlib import Path

from r132_occurrence_import_contract import build_occurrence_rows

ROOT = uuid.UUID("f72805f4-7547-5017-a069-74042708d228")
RUN = "61d65749-dab0-55db-b62a-6834e4fe604d"
CAMPAIGN = "acea-vegfa-dual-autoresearch-20260923-v1"
GEN = {
    "acea": "746677b1-a103-5918-8c85-e1ba8d40dc96",
    "vegfa": "c192805b-c8e9-5c7a-8dc4-2daf87f93388",
}
TARGET = {
    "acea": "6c45ae22-e578-4b3e-a885-dee1f11b6390",
    "vegfa": "8dd7eb39-6c4a-4c3e-bbc3-b6add9aecd35",
}
PRIMARY = [
    "amp_read_log10_mic_um",
    "guruprasad_instability_index",
    "hydrophobic_moment_eisenberg",
    "hydrophobic_ratio_modlamp",
    "llamp_log10_mic_um",
    "macrel_amp_probability",
    "macrel_hemolysis_label",
    "macrel_hemolysis_probability",
    "maximum_hydrophobic_run",
    "net_charge_ph7_4",
    "toxinpred3_hybrid_score",
    "toxinpred3_label",
]
TEXT = {"macrel_hemolysis_label", "toxinpred3_label"}
UNITS = {
    "amp_read_log10_mic_um": "log10(umol/L)",
    "guruprasad_instability_index": "index",
    "hydrophobic_moment_eisenberg": "dimensionless",
    "hydrophobic_ratio_modlamp": "fraction",
    "llamp_log10_mic_um": "log10(umol/L)",
    "macrel_amp_probability": "fraction",
    "macrel_hemolysis_label": None,
    "macrel_hemolysis_probability": "fraction",
    "maximum_hydrophobic_run": "residues",
    "net_charge_ph7_4": "elementary_charge",
    "toxinpred3_hybrid_score": "dimensionless",
    "toxinpred3_label": None,
}
PROV = {
    "amp_read_log10_mic_um": ("mic_potency_amp_read", "amp-read-ec9478b-open-weights-cpu-v1"),
    "guruprasad_instability_index": (
        "physicochemical_developability",
        "2026.08.22-v2-deterministic-supplement",
    ),
    "hydrophobic_moment_eisenberg": ("physicochemical_developability", "2026.08.04-v1"),
    "hydrophobic_ratio_modlamp": ("physicochemical_developability", "2026.08.04-v1"),
    "llamp_log10_mic_um": ("mic_potency", "llamp-bb48daa-esm2-16b0dddc"),
    "macrel_amp_probability": ("hemolysis_risk", "macrel-1.6.1-8c1f732"),
    "macrel_hemolysis_label": ("hemolysis_risk", "macrel-1.6.1-8c1f732"),
    "macrel_hemolysis_probability": ("hemolysis_risk", "macrel-1.6.1-8c1f732"),
    "maximum_hydrophobic_run": (
        "physicochemical_developability",
        "2026.08.22-v2-deterministic-supplement",
    ),
    "net_charge_ph7_4": ("physicochemical_developability", "2026.08.04-v1"),
    "toxinpred3_hybrid_score": ("toxicity_risk", "toxinpred3-corrected-v0.2-dcdba83"),
    "toxinpred3_label": ("toxicity_risk", "toxinpred3-corrected-v0.2-dcdba83"),
}


def read(p):
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def stable(x):
    return hashlib.sha256(
        json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main(a):
    enr = read(a.enriched)
    scores = read(a.score / "candidate_scores.csv")
    amps = read(a.amplify)
    assert len(enr) == 4 and len(scores) == 4 and len(amps) == 4
    sr = {r["action_id"]: r for r in scores}
    ar = {r["candidate_id"]: r for r in amps}
    candidate_ids = {
        r["sequence"]: str(uuid.uuid5(ROOT, f"{CAMPAIGN}:r132:candidate:{r['action_id']}"))
        for r in enr
    }
    actions = {
        t: json.loads((a.requests / f"round132_{t}_request.json").read_text())["action_plans"]
        for t in ("acea", "vegfa")
    }
    generated = {
        t: [
            {
                "action_id": r["action_id"],
                "sequence": r["sequence"],
                "conditional_nll": float(r["conditional_nll"]),
                "conditional_ppl": float(r["conditional_ppl"]),
            }
            for r in enr
            if r["target"] == t
        ]
        for t in ("acea", "vegfa")
    }
    occ = build_occurrence_rows(
        run_id=RUN,
        target_tool_calls=GEN,
        action_plans=actions,
        generated_rows=generated,
        canonical_candidate_by_sequence=candidate_ids,
        root_uuid=str(ROOT),
    )
    imp = str(uuid.uuid5(ROOT, f"{CAMPAIGN}:r132:score-import.v1"))
    candidates = []
    ev = []
    for r in enr:
        action = r["action_id"]
        rr = sr[action]
        amp = ar[action]
        cid = candidate_ids[r["sequence"]]
        eligible = (
            r["display_hard_gate"].lower() == "true" and int(r["dual_reference_support_min"]) >= 2
        )
        for m in PRIMARY:
            plugin, release = PROV[m]
            assert rr[m] != "" and not release.startswith("898fca")
            ev.append(
                {
                    "id": str(uuid.uuid5(ROOT, f"{CAMPAIGN}:r132:evaluation:{action}:{m}")),
                    "candidate_id": cid,
                    "subject_run_id": RUN,
                    "tool_call_id": imp,
                    "authoritative_candidate_id": action,
                    "metric_name": m,
                    "numeric_value": None if m in TEXT else float(rr[m]),
                    "text_value": rr[m] if m in TEXT else None,
                    "unit": UNITS[m],
                    "status": "succeeded",
                    "evidence_role": "primary",
                    "evidence_family": "score_all",
                    "model_release_key": release,
                    "out_of_domain": False,
                    "ood_status": "unknown_not_assessed",
                    "limitations": ["legacy false retained; OOD not assessed"],
                    "raw_source": {
                        "raw_candidate_id": rr["candidate_id"],
                        "provider_plugin": plugin,
                        "provider_contract": release,
                        "metric_artifact": str(a.score / "metrics" / f"{plugin}.json"),
                        "local_score_dir": str(a.score),
                        "local_enriched": str(a.enriched),
                        "remote_root": a.remote,
                    },
                    "candidate_admission_not_implied": not eligible,
                }
            )
        for model, role, fam in [
            ("HemoPI2", "challenger", "hemolysis"),
            ("APEX", "shadow", "activity"),
            ("PeptiVerse", "shadow", "activity"),
        ]:
            ev.append(
                {
                    "id": str(uuid.uuid5(ROOT, f"{CAMPAIGN}:r132:evaluation:{action}:{model}")),
                    "candidate_id": cid,
                    "subject_run_id": RUN,
                    "tool_call_id": imp,
                    "authoritative_candidate_id": action,
                    "metric_name": f"{model}_availability",
                    "numeric_value": None,
                    "text_value": None,
                    "unit": None,
                    "status": "unsupported",
                    "evidence_role": role,
                    "evidence_family": fam,
                    "model_release_key": f"inventory:{model.lower()}:runtime_unavailable:release_unknown",
                    "applicability_status": "runtime_unavailable",
                    "conflict_status": "not_assessed",
                    "out_of_domain": False,
                    "ood_status": "unknown_not_assessed",
                    "limitations": ["runtime unavailable; inventory is not executed scoring"],
                    "raw_source": {
                        "metric_status": str(a.score / "metric_status.csv"),
                        "remote_root": a.remote,
                    },
                    "candidate_admission_not_implied": not eligible,
                }
            )
        diag = {f"submodel_{i}": amp[f"amplify_submodel_{i}_probability"] for i in range(1, 6)}
        ev.append(
            {
                "id": str(uuid.uuid5(ROOT, f"{CAMPAIGN}:r132:evaluation:{action}:AMPlify")),
                "candidate_id": cid,
                "subject_run_id": RUN,
                "tool_call_id": imp,
                "authoritative_candidate_id": action,
                "metric_name": "amplify_probability",
                "numeric_value": float(amp["amplify_probability"]),
                "text_value": amp["amplify_label"],
                "unit": "probability",
                "status": "succeeded",
                "evidence_role": "shadow",
                "evidence_family": "amp_likelihood",
                "model_release_key": "amplify-2.0.1-py36hdfd78af_2",
                "out_of_domain": False,
                "ood_status": "unknown_not_assessed",
                "limitations": ["soft AMP likelihood only"],
                "raw_source": {
                    "amplify_csv": str(a.amplify),
                    "five_submodel_probabilities": diag,
                    "remote_root": a.remote,
                },
                "candidate_admission_not_implied": not eligible,
            }
        )
        candidates.append(
            {
                "id": cid,
                "authoritative_candidate_id": action,
                "source_scorer_candidate_id": rr["candidate_id"],
                "run_id": RUN,
                "sequence": r["sequence"],
                "sequence_sha256": hashlib.sha256(r["sequence"].encode()).hexdigest(),
                "generation": int(r["generation"]),
                "parent_id": r["parent_typed_uuid"],
                "parent_sequence": r["parent_sequence"],
                "parent_generation": int(r["parent_generation"]),
                "generator_tool_call_id": GEN[r["target"]],
                "target_role": r["target"],
                "target_uuid": TARGET[r["target"]],
                "dual_support_min": int(r["dual_reference_support_min"]),
                "support_acea": int(r["support_acea"]),
                "support_vegfa": int(r["support_vegfa"]),
                "display_eligible": r["display_hard_gate"].lower() == "true",
                "candidate_admission_eligible": eligible,
                "raw_occurrence_ids": [x["id"] for x in occ if x["candidate_id"] == cid],
            }
        )
    arts = {
        "enriched": str(a.enriched),
        "enriched_sha256": sha(a.enriched),
        "score_dir": str(a.score),
        "amplify_csv": str(a.amplify),
        "alias_map": str(a.alias_map),
        "qd_summary": str(a.qd),
        "execution_attempt_audit": str(a.attempt),
        "remote_root": a.remote,
        "remote_final_source": f"{a.remote}/r132_evidence_sources_final_v2",
        "remote_enriched": f"{a.remote}/r132_evidence_sources_final_v2/r132_final_enriched_authoritative.csv",
        "remote_formal": f"{a.remote}/r132_evidence_sources_final_v2/score_all_r116_runner_local_unique/candidate_scores.csv",
        "remote_amplify": f"{a.remote}/r132_evidence_sources_final_v2/amplify_scores_unique.csv",
    }
    inp = {
        "campaign_id": CAMPAIGN,
        "run_id": RUN,
        "round": "r132",
        "source_artifacts": arts,
        "counts_as_scorer_invocation": False,
        "provenance": json.loads(a.attempt.read_text()),
        "candidate_authoritative_ids": [r["action_id"] for r in enr],
    }
    plan = {
        "schema_version": "ampgent.r132.candidate-evaluation-plan.v1",
        "mode": "root_review_required_no_write",
        "plan_status": "complete_scores_pending_root_import",
        "campaign_id": CAMPAIGN,
        "run_id": RUN,
        "round": "r132",
        "score_import_tool_call": {
            "id": imp,
            "tool_name": "ampgent.import_r132_score_evidence",
            "counts_as_scorer_invocation": False,
            "idempotency_key": stable({"operation": f"{RUN}:r132:score-import.v1"}),
            "input_json": inp,
            "input_sha256": stable(inp),
        },
        "source_artifacts": arts,
        "candidates": candidates,
        "evaluations": ev,
        "candidate_occurrences": occ,
        "planned_inserts": {
            "tool_calls": 1,
            "candidates": 4,
            "evaluations": 64,
            "candidate_occurrences": 4,
        },
        "transaction_guards": {
            "run_id": RUN,
            "generator_calls": list(GEN.values()),
            "parent_same_run_sequence_generation": True,
            "occurrence_retry_key": ["tool_call_id", "occurrence_rank"],
            "no_write_until_root_approval": True,
        },
        "operational_evidence": json.loads(a.attempt.read_text()),
        "policy": {
            "ood": "unknown_not_assessed",
            "unavailable": ["HemoPI2", "APEX", "PeptiVerse"],
            "amp_release": "amplify-2.0.1-py36hdfd78af_2",
        },
    }
    a.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(a.output),
                "candidates": 4,
                "evaluations": 64,
                "occurrences": 4,
                "primary": 48,
                "unsupported": 12,
                "amp": 4,
                "eligible": [
                    r["action_id"]
                    for r in enr
                    if r["display_hard_gate"].lower() == "true"
                    and int(r["dual_reference_support_min"]) >= 2
                ],
                "import_id": imp,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--enriched", type=Path, required=True)
    p.add_argument("--score", type=Path, required=True)
    p.add_argument("--amplify", type=Path, required=True)
    p.add_argument("--requests", type=Path, required=True)
    p.add_argument("--alias-map", type=Path, required=True)
    p.add_argument("--qd", type=Path, required=True)
    p.add_argument("--attempt", type=Path, required=True)
    p.add_argument("--remote", required=True)
    p.add_argument("--output", type=Path, required=True)
    main(p.parse_args())
