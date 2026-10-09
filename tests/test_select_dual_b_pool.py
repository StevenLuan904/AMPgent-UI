import sys
import csv
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.select_dual_b_pool import comparison, dominates, gate, main, select, _enrich


def row(cid, q, hemo=0.2, toxin=0.1, instability=10.0, cell="q1-h1-m1-l2", support=2, display=True):
    return {
        "candidate_id": cid, "sequence": cid, "quality": str(q),
        "macrel_hemolysis_probability": str(hemo), "macrel_hemolysis_label": "low",
        "toxinpred3_hybrid_score": str(toxin), "guruprasad_instability_index": str(instability),
        "toxinpred3_label": "Non-Toxin",
        "formal12": "true", "display_hard_gate": str(display).lower(),
        "dual_reference_support_min": str(support), "cell_id": cell,
    }


class DualBSelectionTests(unittest.TestCase):
    def test_gate_requires_all_frozen_hard_gates(self):
        ok, reasons = gate(row("good", -1.0))
        self.assertTrue(ok)
        bad_row = row("bad", -1.0, hemo=0.6, support=1, display=False)
        bad_row["toxinpred3_label"] = "Toxin"
        bad, reasons = gate(bad_row)
        self.assertFalse(bad)
        self.assertEqual(set(reasons), {"display", "dual_support", "qd_quality"})

    def test_dominance_is_unweighted_four_axis(self):
        better = row("better", -1.0, hemo=0.2, toxin=0.1, instability=10)
        worse = row("worse", -1.2, hemo=0.3, toxin=0.2, instability=12)
        self.assertTrue(dominates(better, worse))
        self.assertFalse(dominates(worse, better))

    def test_prior_nonfront_roles_are_retained_and_no_challenger_is_replacement(self):
        prior = [row("old-a", -0.2), row("old-b", -0.3)]
        fresh = [row("challenger", -1.0, cell="q9-h1-m1-l2")]
        result = select(fresh, prior, [row("archive", -0.4, cell="q9-h1-m1-l2")])
        self.assertEqual(result["pareto_front_ids"], ["challenger"])
        self.assertEqual(result["replacement_count"], 0)
        self.assertEqual(result["selected_ids"], ["old-a", "old-b"])

    def test_strict_dominance_replaces_only_when_old_cell_has_two_occurrences(self):
        prior = [row("old", -1.2, hemo=0.4, toxin=0.3, instability=20, cell="q1-h1-m1-l2"), row("old2", -1.1, hemo=0.4, toxin=0.3, instability=20, cell="q1-h1-m1-l2")]
        fresh = [row("new", -1.0, hemo=0.2, toxin=0.1, instability=10)]
        result = select(fresh, prior, [row("old", -1.2, cell="q1-h1-m1-l2"), row("x2", -1.1, cell="q1-h1-m1-l2")])
        self.assertEqual(result["replacement_count"], 1)
        self.assertEqual(result["selected_ids"], ["new", "old2"])

    def test_one_challenger_replaces_only_one_old_slot_and_a_is_protected(self):
        prior = [row("old-a", -1.2, cell="q1-h1-m1-l2"), row("old-b", -1.1, cell="q1-h1-m1-l2")]
        fresh = [row("new", -1.0)]
        archive = [row("old-a", -1.2), row("old-b", -1.1)]
        result = select(fresh, prior, archive, {("old-a", "old-a")})
        self.assertEqual(result["replacement_count"], 1)
        self.assertEqual(result["selected_ids"].count("new"), 1)
        self.assertIn("old-a", result["selected_ids"])

    def test_missing_metric_is_pending_not_dominance(self):
        x = row("x", -1.0)
        y = row("y", -1.1)
        del y["toxinpred3_hybrid_score"]
        self.assertFalse(dominates(x, y))
        self.assertIn("comparison_pending:toxinpred3_hybrid_score", comparison(x, y)[1])

    def test_epsilon_noise_is_not_strict_improvement(self):
        x = row("x", -1.0)
        y = row("y", -1.0000000001)
        self.assertFalse(dominates(x, y))

    def test_small_value_relative_tolerance_is_not_scaled_by_one(self):
        x, y = row("x", -0.1), row("y", -0.10000001)
        self.assertFalse(dominates(x, y))

    def test_prior_sequence_is_excluded_even_when_candidate_id_differs(self):
        old = row("old", -1.0)
        challenger = row("new", -1.1)
        challenger["sequence"] = old["sequence"]
        result = select([challenger], [old], [old])
        self.assertIn("sequence_already_in_B", result["excluded"][0]["reasons"])

    def test_conflicting_archive_scalar_stops_decision(self):
        a = row("same", -1.0)
        b = row("same", -0.5)
        result = select([], [], [a, b])
        self.assertEqual(result["decision_status"], "decision_not_ready")

    def test_duplicate_sequence_is_excluded(self):
        first, second = row("x", -1.0), row("y", -1.1)
        second["sequence"] = first["sequence"]
        result = select([first, second], [], [])
        self.assertEqual(result["gated_count"], 1)
        self.assertIn("duplicate_sequence_in_pool", result["excluded"][0]["reasons"])

    def test_cli_round_metadata_is_parameterized_and_sources_are_recorded(self):
        old, fresh = row("old", -1.0), row("fresh", -1.1)
        old["proposal_round"] = "115"
        fresh["proposal_round"] = "117"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fields = sorted(set(old) | set(fresh))
            for name, values in (("archive.csv", [old]), ("current.csv", [fresh])):
                with (root / name).open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(values)
            prior_b = root / "prior_b.json"
            prior_b.write_text(json.dumps({"entries": [old]}), encoding="utf-8")
            prior_a = root / "prior_a.json"
            prior_a.write_text(json.dumps({"entries": []}), encoding="utf-8")
            output, successor, receipt = root / "audit.json", root / "successor.json", root / "receipt.json"
            old_argv = sys.argv
            try:
                sys.argv = ["select_dual_b_pool", "--current", str(root / "current.csv"), "--prior-b", str(prior_b), "--archive", str(root / "archive.csv"), "--prior-a", str(prior_a), "--proposal-round", "117", "--output", str(output), "--successor", str(successor), "--receipt", str(receipt)]
                main()
            finally:
                sys.argv = old_argv
            successor_payload = json.loads(successor.read_text(encoding="utf-8"))
            receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(successor_payload["proposal_round"], 117)
            self.assertEqual(receipt_payload["proposal_round"], 117)
            self.assertNotIn("r115_parent_binding_correction", receipt_payload)
            self.assertIn("current", successor_payload["source_metadata"])

    def test_missing_instability_is_filled_from_exact_id_sequence_evidence(self):
        target = row("candidate", -1.0, instability=None)
        target["guruprasad_instability_index"] = ""
        evidence = row("candidate", -1.0, instability=16.0)
        enriched = _enrich([target], [evidence])[0]
        self.assertEqual(float(enriched["guruprasad_instability_index"]), 16.0)

    def test_finite_instability_is_never_overwritten_by_evidence(self):
        target = row("candidate", -1.0, instability=20.0)
        evidence = row("candidate", -1.0, instability=16.0)
        self.assertEqual(float(_enrich([target], [evidence])[0]["guruprasad_instability_index"]), 20.0)

    def test_nonmatching_identity_does_not_fill_instability(self):
        target = row("candidate", -1.0, instability=None)
        target["guruprasad_instability_index"] = ""
        evidence = row("other", -1.0, instability=16.0)
        self.assertEqual(_enrich([target], [evidence])[0]["guruprasad_instability_index"], "")

    def test_conflicting_finite_evidence_is_marked_not_arbitrarily_selected(self):
        target = row("candidate", -1.0, instability=None)
        target["guruprasad_instability_index"] = ""
        evidence_a = row("candidate", -1.0, instability=16.0)
        evidence_b = row("candidate", -1.0, instability=17.0)
        enriched = _enrich([target], [evidence_a, evidence_b])[0]
        self.assertEqual(enriched["guruprasad_instability_index"], "")
        self.assertIn("guruprasad_evidence_conflict", enriched)


if __name__ == "__main__":
    unittest.main()
