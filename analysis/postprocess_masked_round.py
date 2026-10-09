"""Model-free parameterized postprocess for masked rounds.

Reads already-produced formal/AMPlify/support artifacts, preserves every raw
occurrence, and writes a derived archive to an explicit output directory.
It never launches a scorer or mutates the input/prior archive.
"""
from __future__ import annotations
import argparse, csv, hashlib, json
from pathlib import Path
from analysis.dual_qd_memory_20260923 import _cell
from analysis.r129_postprocess_contract import (apply_dual_support, compute_parent_deltas,
    join_exact_parent, numeric_stats, select_one_per_cell, synchronize_selected_outputs,
    validate_generation_pair)


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = []
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--round-dir", type=Path, required=True)
    p.add_argument("--input-csv", type=Path, required=True)
    p.add_argument("--actions-csv", type=Path, required=True)
    p.add_argument("--formal-csv", type=Path, required=True)
    p.add_argument("--amplify-csv", type=Path, required=True)
    p.add_argument("--support-calibrated-csv", type=Path, required=True)
    p.add_argument("--prior-archive", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    inputs = {(r["sequence"], r["target"]): r for r in read_csv(args.input_csv)}
    actions = {r["action_id"]: r for r in read_csv(args.actions_csv)}
    formal = read_csv(args.formal_csv)
    amp = {(r.get("sequence", ""), r.get("candidate_id", "").split("-")[1] if "-" in r.get("candidate_id", "") else ""): r for r in read_csv(args.amplify_csv)}
    prior = read_csv(args.prior_archive)
    score_rows = []
    for s in formal:
        raw = inputs[(s["sequence"], s["target"])]
        a = actions[raw["action_id"]]
        child_generation = a.get("lineage_generation", raw.get("generation", ""))
        parent_generation = a.get("parent_lineage_generation", raw.get("parent_generation", ""))
        validate_generation_pair(parent_generation, child_generation)
        x = dict(s)
        x.update({"candidate_id": a["action_id"], "source_scorer_candidate_id": s.get("candidate_id", ""), "action_id": a["action_id"], "sequence": raw["sequence"], "target": raw["target"], "branch_key": raw["target"], "proposal_round": a.get("proposal_round", args.round_dir.name), "parent_candidate_id": a.get("parent_authoritative_candidate_id", a.get("parent_candidate_id", "")), "parent_sequence": a.get("parent_sequence", ""), "parent_generation": parent_generation, "lineage_generation": child_generation, "generation": child_generation, "parent_typed_uuid": a.get("parent_typed_uuid", ""), "seed": raw.get("seed", a.get("seed", "")), "mutation_positions_json": json.dumps(json.loads(a.get("mutation_positions", "[]")) if a.get("mutation_positions", "").startswith("[") else [int(a.get("mutation_positions", "0"))], separators=(",", ":"))})
        x["mutation_positions"] = x["mutation_positions_json"]
        x["formal12"] = str(x.get("formal_12_complete", "")).lower() == "true"; x["display_hard_gate"] = str(x.get("display_eligible", "")).lower() == "true"; x["all_finite"] = "True"
        x["quality"] = -max(float(x["llamp_log10_mic_um"]), float(x["amp_read_log10_mic_um"]))
        cell, desc = _cell(x); x["cell_id"] = cell or "unresolved"; x.update({k: str(v) for k, v in desc.items()})
        ar = amp.get((x["sequence"], x["target"]), {})
        for k in ("amplify_probability", "amplify_label", "amplify_log_scaled_score", "amplify_submodel_1_probability", "amplify_submodel_2_probability", "amplify_submodel_3_probability", "amplify_submodel_4_probability", "amplify_submodel_5_probability"): x[k] = ar.get(k, "")
        x["fixed_cell_selected"] = "False"; x["ood_status"] = "unknown_not_assessed"
        score_rows.append(x)
    calibrated = read_csv(args.support_calibrated_csv)
    batch = apply_dual_support(score_rows, calibrated)
    for x in batch:
        x["archive_status"] = "eligible" if x["formal12"] and x["display_hard_gate"] and int(x["dual_reference_support_min"]) >= 2 and x["cell_id"] != "unresolved" else "pending_support_or_gate"
        x["quality_eligible"] = str(x["archive_status"] == "eligible")
        x["support_basis"] = "r113_frozen_dual_domain"
        parent = join_exact_parent(prior, x["parent_candidate_id"], x["parent_sequence"])
        if parent is None:
            x["parent_delta_phi"] = ""; x["parent_delta_objectives"] = ""; x["delta_phi_status"] = "unknown_parent_identity"; x["parent_domination_status"] = "unknown_parent_identity"
        else:
            d = compute_parent_deltas(x, parent); x["parent_delta_phi"] = json.dumps(d["parent_delta_phi"], separators=(",", ":")); x["parent_delta_objectives"] = json.dumps(d["parent_delta_objectives"], separators=(",", ":")); x["delta_phi_status"] = "computed_exact_parent_candidate_and_sequence"; x["parent_domination_status"] = d["parent_domination_status"]
    selected, qd = select_one_per_cell(prior, batch)
    by_id = {r["candidate_id"]: r for r in selected}
    synced, _ = synchronize_selected_outputs(batch, selected)
    for x in synced: x["fixed_cell_selected"] = by_id[x["candidate_id"]].get("fixed_cell_selected", "False")
    successor = []
    for r in prior + synced:
        x = dict(r); x["fixed_cell_selected"] = by_id.get(x.get("candidate_id"), x).get("fixed_cell_selected", x.get("fixed_cell_selected", "False")); successor.append(x)
    write_csv(args.output_dir / "final_enriched_authoritative.csv", synced); write_csv(args.output_dir / "archive_successor_appendonly.csv", successor); write_csv(args.output_dir / "current_for_B.csv", synced)
    selected_count = sum(x.get("fixed_cell_selected") == "True" for x in successor)
    cells = {x.get("cell_id") for x in successor if x.get("fixed_cell_selected") == "True"}
    if selected_count != len(cells) or selected_count != qd["selected_after"]: raise AssertionError("selected-per-cell contract failed")
    stat_cols = {"llamp_log10_mic_um": ("llamp_log10_mic_um", "log10(um)", "min"), "amp_read_log10_mic_um": ("amp_read_log10_mic_um", "log10(um)", "min"), "macrel_amp_probability": ("macrel_amp_probability", "fraction", "max"), "macrel_hemolysis_probability": ("macrel_hemolysis_probability", "fraction", "min"), "toxinpred3_hybrid_score": ("toxinpred3_hybrid_score", "dimensionless", "min"), "guruprasad_instability_index": ("guruprasad_instability_index", "index", "min"), "maximum_hydrophobic_run": ("maximum_hydrophobic_run", "residues", "context_only"), "net_charge_ph7_4": ("net_charge_ph7_4", "e", "context_only"), "hydrophobic_ratio_modlamp": ("hydrophobic_ratio_modlamp", "fraction", "context_only"), "hydrophobic_moment_eisenberg": ("hydrophobic_moment_eisenberg", "dimensionless", "context_only")}
    summary = {"schema": "masked-round-postprocess.v1", "round_dir": str(args.round_dir), "input_sha256": sha(args.input_csv), "raw_count": len(successor), "unique_count": len({x["sequence"] for x in successor}), "selected_cells": selected_count, "batch_quality_eligible": sum(x["archive_status"] == "eligible" for x in synced), "label_counts": {"toxinpred3": {"Non-Toxin": sum(str(x.get("toxinpred3_label", "")).lower() == "non-toxin" for x in synced)}, "macrel_hemolysis": {"low": sum(str(x.get("macrel_hemolysis_label", "")).lower() == "low" for x in synced)}}, "stats_all_unique": numeric_stats(synced, stat_cols), "qd": qd, "model_calls": 0}
    (args.output_dir / "replay_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__": main()
