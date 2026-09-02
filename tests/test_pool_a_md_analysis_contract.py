import ast
from pathlib import Path


def test_pool_a_md_analysis_emits_required_interface_contract():
    source = (Path(__file__).parents[1] / "analysis/analyze_pool_a_md.py").read_text()
    ast.parse(source)
    for key in (
        "interface_rmsd_nm",
        "key_contacts",
        "hydrogen_bond_occupancy",
        "salt_bridge_occupancy",
        "water_bridge_occupancy",
        "peptide_departed",
    ):
        assert key in source
    assert "interaction-stride" in source
    assert "anchor_molecules=receptor_molecules" in source
    assert "other_molecules=peptide_molecules" in source
    assert "interaction_source = traj[:]" in source
    assert '"schema_version": "ampgent.pool-a-md-interface-analysis.2"' in source
    assert "frame_has_water_bridge" in source
    assert "set(map(int, receptor_waters)) & set(map(int, peptide_waters))" in source
