from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from pepagent.autoresearch_quality_diversity import BehaviorSpacePolicy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))


def _module():
    path = (
        Path(__file__).resolve().parents[1]
        / "analysis"
        / "generate_fgf2_pepflow_source_expansion.py"
    )
    spec = importlib.util.spec_from_file_location("fgf2_pepflow_source_expansion", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_donor_rows_keep_real_artifact_provenance(tmp_path: Path) -> None:
    module = _module()
    donor = tmp_path / "donors.csv"
    donor.write_text(
        "donor_candidate_id,donor_source,donor_sequence,donor_fragment\n"
        "pepflow-1,PepFlow,TVGG,G\n",
        encoding="utf-8",
    )
    rows = module.load_pepflow_donors(donor)
    assert len(rows) == 1
    assert rows[0]["donor_artifact"] == str(donor)
    assert rows[0]["donor_row_number"] == "2"
    assert len(rows[0]["donor_row_sha256"]) == 64


def test_generation_is_parent_balanced_and_targets_empty_cells() -> None:
    module = _module()
    module._POLICY = BehaviorSpacePolicy.model_validate(
        json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "reports/fgf2_pepglad_source_expansion_20260904_run1/qd_summary.json"
            ).read_text(encoding="utf-8")
        )["policy"]
    )
    parents = [
        {
            "candidate_id": f"parent-{index}",
            "parent_run_id": "run",
            "sequence": sequence,
            "sequence_sha256": module.sha256_text(sequence),
            "qd_cell": "q3-h0-m1-l2",
            "display_eligible": "true",
            "activity_support_calibrated": "3",
        }
        for index, sequence in enumerate(
            ("EPRASGEGGTTTHPYYGT", "IPRASGEGGTTTHPYYGT", "KPRASGEGGTTTHPYYGT")
        )
    ]
    donors = [
        {
            "donor_candidate_id": "pepflow-1",
            "donor_source": "PepFlow",
            "donor_sequence": "TVGG",
            "donor_fragment": "G",
            "donor_artifact": "artifact",
            "donor_row_number": "2",
            "donor_row_sha256": "a" * 64,
        }
    ]
    empty = {
        "q2-h0-m1-l2",
        "q2-h1-m3-l2",
        "q2-h2-m0-l2",
        "q3-h0-m0-l2",
        "q3-h0-m1-l2",
        "q3-h0-m2-l2",
        "q3-h0-m3-l2",
        "q3-h1-m1-l2",
        "q3-h1-m2-l2",
        "q3-h1-m3-l2",
        "q3-h2-m0-l2",
        "q3-h2-m2-l2",
        "q4-h0-m2-l2",
        "q4-h1-m2-l2",
    }
    rows = module.build_proposals(parents, donors, set(), set(), empty, limit=12)
    assert len(rows) == 12
    assert {row["parent_candidate_id"] for row in rows} == {
        "parent-0",
        "parent-1",
        "parent-2",
    }
    assert all(row["target_cell_hit_preflight"] == "true" for row in rows)
    assert all(row["donor_source"] == "PepFlow" for row in rows)
