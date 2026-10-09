"""Model-free parameterized postprocess for masked rounds.

Reads already-produced formal/AMPlify/support artifacts, preserves every raw
occurrence, and writes a derived archive to an explicit output directory.
It never launches a scorer or mutates the input/prior archive.
"""
from __future__ import annotations
import argparse, csv, hashlib, json
from collections import Counter
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


def occurrence_aliases(raw_rows: list[dict]) -> list[dict]:
    """Assign one canonical action per global peptide sequence."""
    canonical = {}
    out = []
    for row in raw_rows:
        key = row.get("sequence", ""); aid = row.get("action_id", "")
        cid = canonical.setdefault(key, aid)
        out.append({"raw_action_id": aid, "canonical_action_id": cid, "sequence": key, "target": row.get("target", ""), "duplicate_of": "" if cid == aid else cid})
    return out


def resolve_amplify(amp_by_id: dict, amp_by_seq: dict, action_id: str, scorer_id: str, sequence: str, target: str):
    """Resolve AMP by identity, then exact sequence; reject failed/mismatched rows."""
    row = amp_by_id.get(action_id) or amp_by_id.get(scorer_id) or amp_by_seq.get(sequence)
    if not row or str(row.get("sequence", sequence)) != sequence:
        return {}, "missing_or_failed"
    status = str(row.get("status", row.get("amplify_status", row.get("result_status", "success")))).strip().lower()
    if status in {"failed", "failure", "error", "unavailable", "not_assessed", "missing", "missing_or_failed"}:
        return {}, "missing_or_failed"
    required = ("amplify_probability", "amplify_submodel_1_probability", "amplify_submodel_2_probability", "amplify_submodel_3_probability", "amplify_submodel_4_probability", "amplify_submodel_5_probability")
    if any(str(row.get(k, "")).strip() == "" for k in required):
        return {}, "missing_or_failed"
    return row, "success"


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
    p.add_argument("--compare-archive", type=Path)
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_rows = read_csv(args.input_csv)
    actions = {r["action_id"]: r for r in read_csv(args.actions_csv)}
    formal = read_csv(args.formal_csv)
    formal_by_id = {r.get("candidate_id", ""): r for r in formal}
    formal_by_seq = {r.get("sequence", ""): r for r in formal}
    amp_rows = read_csv(args.amplify_csv)
    amp_by_id = {r.get("candidate_id", ""): r for r in amp_rows}
    amp_by_seq = {r.get("sequence", ""): r for r in amp_rows}
    prior = read_csv(args.prior_archive)
    prior_sequences = {r.get("sequence", "") for r in prior}
    aliases_by_action = {r["raw_action_id"]: r for r in occurrence_aliases(raw_rows)}
    score_rows = []
    aliases = []
    for raw in raw_rows:
        alias = aliases_by_action[raw["action_id"]]; canonical_action = alias["canonical_action_id"]
        s = formal_by_id.get(canonical_action) or formal_by_seq.get(raw["sequence"])
        if s is None: raise ValueError(f"missing formal score for raw occurrence {raw.get('action_id')}")
        a = actions[raw["action_id"]]
        child_generation = a.get("lineage_generation", raw.get("generation", ""))
        parent_generation = a.get("parent_lineage_generation", raw.get("parent_generation", ""))
        validate_generation_pair(parent_generation, child_generation)
        x = dict(s)
        x.update({"candidate_id": a["action_id"], "canonical_candidate_id": canonical_action, "duplicate_of": alias["duplicate_of"], "source_scorer_candidate_id": s.get("candidate_id", ""), "action_id": a["action_id"], "sequence": raw["sequence"], "target": raw["target"], "branch_key": raw["target"], "proposal_round": a.get("proposal_round", args.round_dir.name), "parent_candidate_id": a.get("parent_authoritative_candidate_id", a.get("parent_candidate_id", "")), "parent_sequence": a.get("parent_sequence", ""), "parent_generation": parent_generation, "lineage_generation": child_generation, "generation": child_generation, "parent_typed_uuid": a.get("parent_typed_uuid", ""), "seed": raw.get("seed", a.get("seed", "")), "conditional_nll": raw.get("conditional_nll", ""), "conditional_ppl": raw.get("conditional_ppl", ""), "mutation_positions_json": json.dumps(json.loads(a.get("mutation_positions", "[]")) if a.get("mutation_positions", "").startswith("[") else [int(a.get("mutation_positions", "0"))], separators=(",", ":"))})
        x["mutation_positions"] = x["mutation_positions_json"]
        numeric_fields = ("llamp_log10_mic_um", "amp_read_log10_mic_um", "macrel_amp_probability", "macrel_hemolysis_probability", "toxinpred3_hybrid_score", "guruprasad_instability_index", "maximum_hydrophobic_run", "net_charge_ph7_4", "hydrophobic_ratio_modlamp", "hydrophobic_moment_eisenberg")
        finite = True
        for key in numeric_fields:
            try: finite = finite and __import__('math').isfinite(float(x.get(key, "")))
            except (TypeError, ValueError): finite = False
        labels_present = bool(str(x.get("toxinpred3_label", "")).strip()) and bool(str(x.get("macrel_hemolysis_label", "")).strip())
        formal_complete = str(x.get("formal_12_complete", "")).lower() == "true" and finite and labels_present
        try: guru_safe = float(x.get("guruprasad_instability_index", "")) <= 50
        except (TypeError, ValueError): guru_safe = False
        try: hemolysis_safe = float(x.get("macrel_hemolysis_probability", "")) <= 0.5
        except (TypeError, ValueError): hemolysis_safe = False
        toxin_safe = str(x.get("toxinpred3_label", "")).strip().lower() == "non-toxin"
        macrel_safe = str(x.get("macrel_hemolysis_label", "")).strip().lower() == "low"
        x["all_finite"] = str(finite); x["formal12"] = str(formal_complete); x["formal_labels_complete"] = str(labels_present); x["display_hard_gate"] = str(formal_complete and toxin_safe and macrel_safe and hemolysis_safe and guru_safe)
        x["quality"] = -max(float(x["llamp_log10_mic_um"]), float(x["amp_read_log10_mic_um"])) if finite else ""
        cell, desc = _cell(x); x["cell_id"] = cell or "unresolved"; x.update({k: str(v) for k, v in desc.items()})
        ar, amp_status = resolve_amplify(amp_by_id, amp_by_seq, canonical_action, s.get("candidate_id", ""), x["sequence"], x["target"])
        for k in ("amplify_probability", "amplify_label", "amplify_log_scaled_score", "amplify_submodel_1_probability", "amplify_submodel_2_probability", "amplify_submodel_3_probability", "amplify_submodel_4_probability", "amplify_submodel_5_probability"): x[k] = ar.get(k, "")
        x["amplify_status"] = amp_status; x["fixed_cell_selected"] = "False"; x["ood_status"] = "unknown_not_assessed"; x["history_replay"] = str(x["sequence"] in prior_sequences)
        aliases.append({**alias, "history_replay": x["history_replay"]})
        score_rows.append(x)
    calibrated = read_csv(args.support_calibrated_csv)
    # Fan out frozen calibration witnesses to raw aliases by sequence/domain;
    # never copy an origin domain into the other domain.
    cal_by_seq_domain = {(r.get("sequence", ""), r.get("branch_key", "")): r for r in calibrated}
    cal_fanned = []
    for x in score_rows:
        for domain in ("acea", "vegfa"):
            witness = cal_by_seq_domain.get((x["sequence"], domain))
            if witness is None: raise ValueError(f"missing frozen dual witness: {x['action_id']}/{domain}")
            y = dict(witness); y["action_id"] = x["action_id"]; y["candidate_id"] = x["action_id"] + "__support_" + domain; cal_fanned.append(y)
    calibrated = cal_fanned
    batch = apply_dual_support(score_rows, calibrated)
    for x in batch:
        x["archive_status"] = "duplicate_alias" if x.get("duplicate_of") else ("historical_replay" if x["history_replay"] == "True" else ("eligible" if x["formal12"] == "True" and x["display_hard_gate"] == "True" and int(x["dual_reference_support_min"]) >= 2 and x["cell_id"] != "unresolved" else "pending_support_or_gate"))
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
    write_csv(args.output_dir / "occurrence_to_unique_alias_map.csv", aliases)
    selected_count = sum(x.get("fixed_cell_selected") == "True" for x in successor)
    cells = {x.get("cell_id") for x in successor if x.get("fixed_cell_selected") == "True"}
    if selected_count != len(cells) or selected_count != qd["selected_after"]: raise AssertionError("selected-per-cell contract failed")
    stat_cols = {"llamp_log10_mic_um": ("llamp_log10_mic_um", "log10(um)", "min"), "amp_read_log10_mic_um": ("amp_read_log10_mic_um", "log10(um)", "min"), "macrel_amp_probability": ("macrel_amp_probability", "fraction", "max"), "macrel_hemolysis_probability": ("macrel_hemolysis_probability", "fraction", "min"), "toxinpred3_hybrid_score": ("toxinpred3_hybrid_score", "dimensionless", "min"), "guruprasad_instability_index": ("guruprasad_instability_index", "index", "min"), "maximum_hydrophobic_run": ("maximum_hydrophobic_run", "residues", "context_only"), "net_charge_ph7_4": ("net_charge_ph7_4", "e", "context_only"), "hydrophobic_ratio_modlamp": ("hydrophobic_ratio_modlamp", "fraction", "context_only"), "hydrophobic_moment_eisenberg": ("hydrophobic_moment_eisenberg", "dimensionless", "context_only")}
    unique_batch = list({x.get("sequence", ""): x for x in reversed(synced)}.values())
    def labels(field): return dict(Counter((str(x.get(field, "")).strip() or "__missing__") for x in unique_batch))
    summary = {"schema": "masked-round-postprocess.v1", "round_dir": str(args.round_dir), "input_sha256": sha(args.input_csv), "raw_count": len(successor), "unique_count": len({x.get("sequence", "") for x in successor}), "batch_raw_quality_eligible": sum(x["archive_status"] == "eligible" for x in synced), "batch_quality_eligible": sum(x["archive_status"] == "eligible" for x in unique_batch), "selected_cells": selected_count, "label_counts": {"toxinpred3": labels("toxinpred3_label"), "macrel_hemolysis": labels("macrel_hemolysis_label")}, "amplify_status_counts": dict(Counter(x.get("amplify_status", "__missing__") for x in synced)), "stats_all_unique": numeric_stats(unique_batch, stat_cols), "qd": qd, "model_calls": 0}
    if args.compare_archive:
        old = {r.get("candidate_id"): r for r in read_csv(args.compare_archive)}
        compare_fields = ("candidate_id", "sequence", "quality", "cell_id", "formal12", "display_hard_gate", "support_count", "quality_eligible", "fixed_cell_selected", "generation", "parent_generation", "parent_sequence", "parent_delta_phi", "parent_delta_objectives", "conditional_nll", "conditional_ppl")
        def norm(v):
            s = str(v)
            return s.lower() if s.lower() in ("true", "false") else s
        ids_old, ids_new = set(old), {r.get("candidate_id") for r in successor}
        missing_ids, extra_ids = sorted(ids_old - ids_new), sorted(ids_new - ids_old)
        numeric_compare = {"quality", "support_count"}
        mismatches = []
        for r in successor:
            cid = r.get("candidate_id")
            if cid not in old: continue
            for k in compare_fields:
                ov, nv = old[cid].get(k, ""), r.get(k, "")
                try: same = abs(float(ov) - float(nv)) <= 1e-9 if k in numeric_compare else norm(ov) == norm(nv)
                except (TypeError, ValueError): same = norm(ov) == norm(nv)
                if not same: mismatches.append({"candidate_id": cid, "field": k, "old": ov, "new": nv})
        (args.output_dir / "comparison.json").write_text(json.dumps({"old_rows": len(old), "new_rows": len(successor), "missing_ids": missing_ids, "extra_ids": extra_ids, "fields": compare_fields, "mismatch_count": len(mismatches), "mismatches": mismatches[:20]}, indent=2), encoding="utf-8")
    (args.output_dir / "replay_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__": main()
