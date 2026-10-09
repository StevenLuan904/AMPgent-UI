import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("action_identity", ROOT / "analysis" / "action_identity_validator.py")
mod = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(mod)


def test_r39_dual_mask_has_complete_nonrepeated_positions():
    req = json.loads((ROOT / "config/experiments/round39_prepared/round39_acea_request.json").read_text())
    base = req["action_plans"][0]
    assert mod.validate_action_identity(base, expected_positions=[9, 19])["positions"] == [9, 19]


def test_first_only_normalized_mask_is_rejected():
    req = json.loads((ROOT / "config/experiments/round39_prepared/round39_acea_request.json").read_text())
    bad = dict(req["action_plans"][0], mutation_positions=[9])
    with pytest.raises(ValueError, match="mutation_positions"):
        mod.validate_action_identity(bad, expected_positions=[9, 19])
    with pytest.raises(ValueError, match="repeat"):
        mod.validate_action_identity(dict(bad, mutation_positions=[9, 9]))


def test_r40_donor_parent_id_only_is_rejected():
    req = json.loads((ROOT / "config/experiments/round40_prepared/round40_acea_request.json").read_text())
    bad = dict(req["action_plans"][0])
    bad.pop("donor_candidate_id")
    with pytest.raises(ValueError, match="donor_candidate_id"):
        mod.validate_action_identity(bad)


def test_mask_positions_and_canonical_from_residue_are_checked():
    req = json.loads((ROOT / "config/experiments/round39_prepared/round39_acea_request.json").read_text())
    bad = dict(req["action_plans"][0], mutation_positions=[22])
    with pytest.raises(ValueError, match="exceeds"):
        mod.validate_action_identity(bad)
    bad_aa = dict(req["action_plans"][0], substitutions=[{"position": 9, "from_residue": "A"}])
    with pytest.raises(ValueError, match="canonical"):
        mod.validate_action_identity(bad_aa, expected_positions=[9, 19])


def test_singleton_mask_boundary_positions_are_rejected():
    req = json.loads((ROOT / "config/experiments/round39_prepared/round39_acea_request.json").read_text())
    base = req["action_plans"][0]
    with pytest.raises(ValueError, match="1-based"):
        mod.validate_action_identity(dict(base, mutation_positions=[0]), expected_positions=[0])
    with pytest.raises(ValueError, match="exceeds"):
        mod.validate_action_identity(dict(base, mutation_positions=[22]), expected_positions=[22])


def test_compound_crossover_and_empty_baseline_pass():
    req = json.loads((ROOT / "config/experiments/round40_prepared/round40_acea_request.json").read_text())
    good = req["action_plans"][0]
    out = mod.validate_action_identity(good)
    assert out["donor_interval_1based_inclusive"] == [9, 14]
    assert out["primary_segment"] == "YWVNEG"
    assert out["donor_segment"] == "YTGNTA"
    assert mod.validate_action_identity(dict(good, mutation_positions=[]))["donor_interval_1based_inclusive"] == [9, 14]


def test_r40_compound_external_position_and_unequal_native_segments_pass():
    req = json.loads((ROOT / "config/experiments/round40_prepared/round40_acea_request.json").read_text())
    compound = req["action_plans"][1]
    assert mod.validate_action_identity(compound, expected_positions=[19])["lineage_generation"] == 9
    unequal = dict(compound, crossover={"primary_start": 9, "primary_end": 14, "donor_start": 9, "donor_end": 13}, mutation_positions=[])
    assert mod.validate_action_identity(unequal)["donor_segment"] == "YT GNT".replace(" ", "")
