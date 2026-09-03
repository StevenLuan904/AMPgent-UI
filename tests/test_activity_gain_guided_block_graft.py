from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
    path = Path(__file__).resolve().parents[1] / "analysis" / "activity_gain_guided_block_graft.py"
    spec = importlib.util.spec_from_file_location("activity_gain_guided_block_graft", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_diagnostic_keeps_independent_endpoint_gains_and_safe_supported_donors():
    m = _load()
    donor = {"candidate_id": "d", "sequence": "KKRTAAGGKK", "family_key_80_80": "df", "generator_id": "pepglad", "activity_model_support_count": "2", "display_eligible": "true", "toxinpred3_label": "Non-Toxin", "macrel_hemolysis_label": "low", "guruprasad_instability_index": "20", "target_key": "acea"}
    scored = [{"donor_candidate_id": "d", "donor_block_sequence": "KKR", "block_length": "3", "sequence": "AAGKKGSGEA", "acceptor_start_zero_based": "0", "llamp_log10_mic_um__parent_benefit_percentile": "0.2", "amp_read_log10_mic_um__parent_benefit_percentile": "0", "macrel_amp_probability__parent_benefit_percentile": "0.4"}]
    result = m.diagnose_endpoint_gains(scored, [donor])
    assert result[0]["independent_gain_axes"] == ["llamp", "macrel"]


def test_guided_operator_is_bounded_and_avoids_prior_coordinate():
    m = _load()
    acceptor = {"sequence": "AAGGPGSGEA", "sequence_sha256": "x", "family_key_80_80": "af", "generator_id": "pepflow", "display_eligible": "true", "guruprasad_instability_index": "20", "target_key": "acea"}
    fragment = {"donor_candidate_id": "d", "donor_sequence": "KKRTAAGGKK", "donor_family_key_80_80": "df", "donor_source": "pepglad", "donor_block_sequence": "KKR", "independent_gain_axes": ["llamp"], "llamp_gain": 0.2, "amp_read_gain": 0.0, "macrel_gain": 0.0, "prior_acceptor_start_zero_based": 0}
    proposals = m.generate_guided([acceptor], [fragment], max_total=2)
    assert len(proposals) == 2
    assert all(row["acceptor_start_zero_based"] != 0 for row in proposals)
