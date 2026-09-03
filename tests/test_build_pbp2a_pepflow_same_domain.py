from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.build_pbp2a_pepflow_same_domain import generate, prior_edits, select_pepflow_donors


def parent(candidate_id: str, sequence: str) -> dict[str, str]:
    import hashlib

    return {
        "run_id": "9640722a-0f63-53e4-892d-ae42b0085445",
        "candidate_id": candidate_id,
        "sequence": sequence,
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "generation": "131",
    }


def test_donor_selection_is_pepflow_and_residue_unique() -> None:
    donors = select_pepflow_donors(
        [
            {"donor_source": "PepFlow", "donor_fragment": "GP", "proposal_id": "a"},
            {"donor_source": "PepGLAD", "donor_fragment": "K", "proposal_id": "b"},
            {"donor_source": "PepFlow", "donor_fragment": "GP", "proposal_id": "c"},
        ]
    )
    assert [row["donor_residue"] for row in donors] == ["G"]


def test_same_domain_generation_excludes_prior_edit_and_history() -> None:
    parents = [parent("d67d6f2d-71d7-409e-b222-715f54199d74", "KHRKKWANNKIKVRKVNLDEKK")]
    donors = [{"donor_source": "PepFlow", "donor_fragment": "A", "proposal_id": "p"}]
    prior = {("KHRKKWANNKIKVRKVNLDEKK", 2, "A")}
    rows = generate(
        parents,
        donors,
        history_hashes=set(),
        edits=set(prior),
        generation=132,
        seed=20260904,
        limit=12,
    )
    assert rows
    assert all(row["parent_candidate_id"] == parents[0]["candidate_id"] for row in rows)
    assert all(row["generation"] == 132 for row in rows)
    assert all(len(row["sequence"]) == len(parents[0]["sequence"]) for row in rows)
    assert all(
        (
            row["parent_sequence"],
            row["edit_position_zero_based"],
            row["to_residue"],
        )
        not in prior
        for row in rows
    )


def test_prior_edits_reads_only_single_residue_changes(tmp_path) -> None:
    path = tmp_path / "history.csv"
    path.write_text(
        "parent_sequence,sequence\nABC,ADC\nABC,AEFC\n",
        encoding="utf-8",
    )
    assert prior_edits([path]) == {("ABC", 1, "D")}
