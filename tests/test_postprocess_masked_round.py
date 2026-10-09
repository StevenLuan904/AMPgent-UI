import csv
import json
import sys
from pathlib import Path

from analysis.postprocess_masked_round import main, occurrence_aliases, resolve_amplify
from analysis.r129_postprocess_contract import validate_generation_pair


def test_duplicate_raw_occurrences_keep_canonical_alias():
    rows = [
        {"action_id": "a1", "sequence": "SEQ", "target": "acea"},
        {"action_id": "a2", "sequence": "SEQ", "target": "acea"},
        {"action_id": "v1", "sequence": "SEQ", "target": "vegfa"},
        {"action_id": "v2", "sequence": "SEQ", "target": "vegfa"},
    ]
    out = occurrence_aliases(rows)
    assert [x["canonical_action_id"] for x in out] == ["a1", "a1", "a1", "a1"]
    assert out[1]["duplicate_of"] == "a1" and out[2]["duplicate_of"] == "a1"


def test_amplify_missing_is_explicit_not_default_success():
    row, status = resolve_amplify({"a": {"candidate_id": "a"}}, {}, "missing", "missing", "SEQ", "acea")
    assert row == {} and status == "missing_or_failed"


def test_mixed_parent_generations_are_checked_per_action():
    validate_generation_pair(9, 10)
    validate_generation_pair(10, 11)


def test_cli_global_unique_aliases_failed_amp_and_old_flag_replacement(tmp_path, monkeypatch):
    round_dir = tmp_path / "round"
    formal_path, amp_path, support_path, prior_path = (tmp_path / n for n in ("formal.csv", "amp.csv", "support.csv", "prior.csv"))
    input_path, actions_path = tmp_path / "input.csv", tmp_path / "actions.csv"
    out_dir = tmp_path / "out"
    seqs = ["ACDEFGHIKLMNPQRSTUV", "ACDEFGHIKLMNPQRSTUV", "BCDEFGHIKLMNPQRSTUV", "CCDEFGHIKLMNPQRSTUV"]
    targets = ["acea", "vegfa", "acea", "vegfa"]
    ids = ["a1", "v1", "a2", "v2"]
    def write(path, rows):
        fields = list(dict.fromkeys(k for row in rows for k in row));
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    raw = [{"action_id": i, "sequence": s, "target": t, "seed": str(n), "conditional_nll": "1.0", "conditional_ppl": "2.0"} for n, (i, s, t) in enumerate(zip(ids, seqs, targets))]
    actions = [{"action_id": i, "lineage_generation": "2", "parent_lineage_generation": "1", "parent_authoritative_candidate_id": "", "parent_sequence": "", "mutation_positions": "[1]"} for i in ids]
    def formal(seq):
        return {"candidate_id": "formal-" + seq[0], "sequence": seq, "target": "acea", "formal_12_complete": "True", "display_eligible": "True", "llamp_log10_mic_um": "1", "amp_read_log10_mic_um": "1", "macrel_amp_probability": ".2", "macrel_hemolysis_probability": ".1", "toxinpred3_hybrid_score": "0", "guruprasad_instability_index": "2", "maximum_hydrophobic_run": "3", "net_charge_ph7_4": "5", "hydrophobic_ratio_modlamp": ".2", "hydrophobic_moment_eisenberg": ".2", "toxinpred3_label": "Toxin" if seq.startswith("C") else "Non-Toxin", "macrel_hemolysis_label": "high" if seq.startswith("C") else "low"}
    formals = [formal(s) for s in sorted(set(seqs))]
    amp = [{"candidate_id": "a1", "sequence": seqs[0], "amplify_probability": ".4", "amplify_submodel_1_probability": ".4", "amplify_submodel_2_probability": ".4", "amplify_submodel_3_probability": ".4", "amplify_submodel_4_probability": ".4", "amplify_submodel_5_probability": ".4"}, {"candidate_id": "a2", "sequence": seqs[2], "status": "failed"}, {"candidate_id": "v2", "sequence": seqs[3], "amplify_probability": ".4", "amplify_submodel_1_probability": ".4", "amplify_submodel_2_probability": ".4", "amplify_submodel_3_probability": ".4", "amplify_submodel_4_probability": ".4", "amplify_submodel_5_probability": ".4"}]
    support = [{"action_id": i, "branch_key": d, "sequence": s, "activity_model_support_count_calibrated": "2"} for i, s in zip(ids, seqs) for d in ("acea", "vegfa")]
    prior = [{"candidate_id": "old", "sequence": "OLD", "cell_id": "q5-h1-m2-l2", "quality": "-2", "archive_status": "eligible", "fixed_cell_selected": "True"}]
    for p, rows in ((input_path, raw), (actions_path, actions), (formal_path, formals), (amp_path, amp), (support_path, support), (prior_path, prior)): write(p, rows)
    monkeypatch.setattr(sys, "argv", ["postprocess", "--round-dir", str(round_dir), "--input-csv", str(input_path), "--actions-csv", str(actions_path), "--formal-csv", str(formal_path), "--amplify-csv", str(amp_path), "--support-calibrated-csv", str(support_path), "--prior-archive", str(prior_path), "--output-dir", str(out_dir)])
    main()
    summary = json.loads((out_dir / "replay_summary.json").read_text())
    assert summary["raw_count"] == 5 and summary["unique_count"] == 4
    assert summary["stats_all_unique"]["llamp_log10_mic_um"]["n"] == 3
    assert summary["qd"]["replacement_cells"] == ["q5-h1-m2-l2"]
    rows = list(csv.DictReader((out_dir / "final_enriched_authoritative.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 4 and sum(r["amplify_status"] == "missing_or_failed" for r in rows) == 1
    assert summary["label_counts"]["toxinpred3"]["Toxin"] == 1
