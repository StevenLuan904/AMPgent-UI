from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pepagent.domain.schemas import ExperimentSpec
from pepagent.selection import (
    cheap_diverse_selection,
    diversity_constrained_elites,
    sequence_distance,
)
from pepagent.structures.interface import (
    audit_protein_peptide_interface,
    pose_cluster_fraction,
)


def _atom(serial: int, atom: str, residue: str, chain: str, index: int, x: float) -> str:
    return (
        f"ATOM  {serial:5d} {atom:^4s} {residue:>3s} {chain}{index:4d}    "
        f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}{1.0:6.2f}{20.0:6.2f}          C"
    )


def _interface_pdb(path: Path) -> None:
    lines = [
        _atom(1, "CA", "ALA", "A", 1, 0.0),
        _atom(2, "CA", "GLY", "A", 2, 4.0),
        _atom(3, "CA", "SER", "A", 3, 20.0),
        _atom(4, "CA", "LYS", "B", 1, 2.0),
        _atom(5, "N", "LYS", "B", 1, 2.5),
        _atom(6, "C", "LYS", "B", 1, 3.0),
        _atom(7, "O", "LYS", "B", 1, 3.5),
        "END",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def test_diversity_constrained_selection_keeps_distinct_sequences() -> None:
    proposals = [
        {"sequence": "AAAAAAAAAA", "conditional_ppl": 1.0},
        {"sequence": "AAAAAAAATA", "conditional_ppl": 1.1},
        {"sequence": "RRRRRRRRRR", "conditional_ppl": 2.0},
    ]
    selected = cheap_diverse_selection(proposals, 2, maximum_similarity=0.8)
    assert [item["sequence"] for item in selected] == ["AAAAAAAAAA", "RRRRRRRRRR"]
    assert sequence_distance("AAAA", "AAAT") == 1


def test_elite_order_requires_structure_gate_before_rosetta() -> None:
    candidates = [
        {
            "id": "failed-gate",
            "sequence": "AAAAAAAAAA",
            "metrics": {
                "interface_gate_pass": 0,
                "rosetta_dg_separated_reu": -100,
                "conditional_ppl": 1,
            },
        },
        {
            "id": "passed-gate",
            "sequence": "RRRRRRRRRR",
            "metrics": {
                "interface_gate_pass": 1,
                "pocket_contact_consistency": 0.8,
                "boltz2_pair_iptm_median": 0.3,
                "conditional_ppl": 5,
            },
        },
    ]
    assert diversity_constrained_elites(candidates, 1, 0.8)[0]["id"] == "passed-gate"


def test_unfavorable_rosetta_result_does_not_receive_an_evidence_presence_bonus() -> None:
    candidates = [
        {
            "id": "expensive-negative-result",
            "sequence": "AAAAAAAAAA",
            "metrics": {
                "interface_gate_pass": 0,
                "rosetta_dg_separated_reu": 17.4,
                "pocket_contact_consistency": 1.0,
                "boltz2_pair_iptm_median": 0.20,
                "conditional_ppl": 4.7,
            },
        },
        {
            "id": "stronger-structure-without-rosetta",
            "sequence": "RRRRRRRRRR",
            "metrics": {
                "interface_gate_pass": 0,
                "pocket_contact_consistency": 1.0,
                "boltz2_pair_iptm_median": 0.24,
                "conditional_ppl": 5.0,
            },
        },
    ]
    selected = diversity_constrained_elites(candidates, 1, 0.8)
    assert selected[0]["id"] == "stronger-structure-without-rosetta"


def test_coordinate_interface_audit_and_pose_consistency(tmp_path: Path) -> None:
    first = tmp_path / "first.pdb"
    second = tmp_path / "second.pdb"
    _interface_pdb(first)
    _interface_pdb(second)
    audit = audit_protein_peptide_interface(first, [1, 2])
    assert audit["pocket_contact_count"] == 2
    assert audit["pocket_coverage_fraction"] == 1.0
    assert audit["off_pocket_contact_fraction"] == 0.0
    consistency = pose_cluster_fraction([first, second], threshold_angstrom=0.1)
    assert consistency["largest_cluster_fraction"] == 1.0


def test_autoresearch_contract_rejects_single_seed() -> None:
    with pytest.raises(ValidationError, match="multiple independent Boltz seeds"):
        ExperimentSpec.model_validate(
            {
                "target": {
                    "name": "target",
                    "sequence": "ACDEFGHIKLMNPQRSTVWY",
                    "pocket_residues": [1],
                },
                "autoresearch_enabled": True,
                "generations": 3,
                "boltz_seeds_per_candidate": 1,
                "rosetta_enabled": True,
            }
        )


def test_acea_v3_is_an_exploitation_biased_four_generation_run() -> None:
    spec_path = (
        Path(__file__).parents[1] / "config" / "experiments" / "acea_autoresearch_v3.yaml"
    )
    spec = ExperimentSpec.model_validate(yaml.safe_load(spec_path.read_text(encoding="utf-8")))
    assert spec.generations == 4
    assert spec.mutation_children_per_parent == 4
    assert spec.exploration_candidates_per_length == 1
    assert spec.boltz_force_pocket is True
    assert spec.interface_min_pose_cluster_fraction == pytest.approx(2 / 3)
    assert spec.rosetta_nstruct == 200
