from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "analysis" / "cross_source_block_graft.py"
    spec = importlib.util.spec_from_file_location("cross_source_block_graft", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(
    sequence: str, family: str, source: str, support: int, display: str = "true"
) -> dict[str, str]:
    return {
        "sequence": sequence,
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "family_key_80_80": family,
        "generator_id": source,
        "activity_model_support_count": str(support),
        "display_eligible": display,
        "toxinpred3_label": "Non-Toxin",
        "macrel_hemolysis_label": "low",
        "guruprasad_instability_index": "20",
        "target_key": "acea",
    }


def test_selection_applies_roles_and_family_isolation():
    m = _load()
    acceptor = _row("AAGGPGSGEA", "acceptor-family", "pepflow_model2", 0)
    donor = _row("KKRTAAGGKK", "donor-family", "pepglad", 2)
    assert m.select_acceptors([acceptor]) == [acceptor]
    assert m.select_donors([donor], excluded_families={"acceptor-family"}) == [donor]
    assert (
        m.select_donors(
            [_row("KKRTAAGGKK", "acceptor-family", "pepglad", 2)],
            excluded_families={"acceptor-family"},
        )
        == []
    )


def test_donors_require_display_and_safety_gates():
    m = _load()
    base = _row("KKRTAAGGKK", "d", "pepglad", 2)
    toxin = {**base, "toxinpred3_label": "Toxin"}
    hemolytic = {**base, "macrel_hemolysis_label": "high"}
    not_display = {**base, "display_eligible": "false"}
    safe = {**base, "toxinpred3_label": "Non-Toxin", "macrel_hemolysis_label": "low"}
    assert m.select_donors([safe], excluded_families={"a"}) == [safe]
    assert m.select_donors([toxin, hemolytic, not_display], excluded_families={"a"}) == []


def test_graft_is_bounded_deterministic_and_records_coordinates_and_delta():
    m = _load()
    a = _row("AAGGPGSGEA", "a", "pepflow", 0)
    d = _row("KKRTAAGGKK", "d", "pepglad", 2)
    proposals, actions = m.generate_grafts([a], [d], max_pairs=1, max_proposals_per_pair=3)
    assert len(proposals) == len(actions) == 3
    assert proposals == m.generate_grafts([a], [d], max_pairs=1, max_proposals_per_pair=3)[0]
    for row, action in zip(proposals, actions, strict=True):
        assert 3 <= int(row["block_length"]) <= 5
        assert row["acceptor_family_key_80_80"] == "a"
        assert row["donor_family_key_80_80"] == "d"
        assert row["sequence_sha256"] == hashlib.sha256(row["sequence"].encode()).hexdigest()
        assert row["delta_phi"]
        assert action["acceptor_start_zero_based"] >= 0


def test_hydrophobicity_is_not_a_gate():
    m = _load()
    a = _row("AAAAAAAAAA", "a", "pepflow", 0)
    d = _row("KKKKKKKKKK", "d", "pepmlm-target-conditional", 2)
    proposals, _ = m.generate_grafts([a], [d], max_pairs=1, max_proposals_per_pair=1)
    assert proposals


def test_delta_phi_uses_formal_descriptors_and_qd_axes():
    m = _load()
    row = m._delta_phi("AAGGPGSGEA", "KKRTAAGGKK", "KKGGPGSGEA")
    assert row["axes"] == [
        "net_charge_over_length",
        "hydrophobic_ratio",
        "hydrophobic_moment",
        "length",
    ]
    assert row["acceptor_to_child"][0] != 0.0
