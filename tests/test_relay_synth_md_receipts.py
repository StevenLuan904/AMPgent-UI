import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.relay_synth_md_receipts import materialize

RUN = "22222222-2222-2222-2222-222222222222"
CANDIDATE = "11111111-1111-1111-1111-111111111111"
TOOL = "33333333-3333-3333-3333-333333333333"


def _entry(release, tool=TOOL):
    directory = "interface" if "interface" in release else "mmgbsa"
    return {
        "subject_run_id": RUN,
        "candidate_id": CANDIDATE,
        "model_release_key": release,
        "tool_call_id": tool,
        "inserted_evaluation_count": 11,
        "ingested_at": "2026-09-04T00:00:00Z",
        "files": [
            {
                "uri": (
                    f"/remote/results/acea/{CANDIDATE}/analysis/"
                    f"{directory}/analysis.json"
                )
            }
        ],
    }


def _root(tmp_path: Path, target="acea"):
    candidate = tmp_path / target / CANDIDATE
    candidate.mkdir(parents=True)
    (candidate / "launch_receipt.json").write_text(
        json.dumps(
            {
                "candidate_id": CANDIDATE,
                "subject_run_id": RUN,
                "target_key": target,
            }
        ),
        encoding="utf-8",
    )


def test_writes_interface_and_mmgbsa_and_is_idempotent(tmp_path):
    _root(tmp_path)
    state = {
        "ingested": [
            _entry(
                "openmm_ff14sb_tip3p_1ns-npt_50ns-nvt_interface-pbc_v2"
            ),
            _entry("ambertools26_mmgbsa_igb5_sparse_v1"),
        ]
    }
    assert materialize(state, tmp_path, "fixture") ["written_receipt_count"] == 2
    result = materialize(state, tmp_path, "fixture")
    assert result["existing_idempotent_receipt_count"] == 2


def test_rejects_duplicate_identity_and_payload_drift(tmp_path):
    _root(tmp_path)
    entry = _entry(
        "openmm_ff14sb_tip3p_1ns-npt_50ns-nvt_interface-pbc_v2"
    )
    materialize({"ingested": [entry]}, tmp_path, "fixture")
    with pytest.raises(ValueError, match="duplicate"):
        materialize({"ingested": [entry, entry]}, tmp_path, "fixture")
    receipt = (
        tmp_path / "acea" / CANDIDATE / "analysis" / "interface"
        / "postgresql_ingest_receipt.json"
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["tool_call_id"] = "44444444-4444-4444-4444-444444444444"
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="payload drift"):
        materialize({"ingested": [entry]}, tmp_path, "fixture")


def test_rejects_launch_and_file_identity_drift(tmp_path):
    _root(tmp_path)
    launch = tmp_path / "acea" / CANDIDATE / "launch_receipt.json"
    launch.write_text(
        json.dumps({"candidate_id": CANDIDATE, "subject_run_id": RUN,
                    "target_key": "fgf2"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="launch identity drift"):
        materialize({"ingested": [_entry(
            "openmm_ff14sb_tip3p_1ns-npt_50ns-nvt_interface-pbc_v2"
        )]}, tmp_path, "fixture")


def test_accepts_ingester_files_mapping_for_already_complete_entry(tmp_path):
    _root(tmp_path)
    release = "openmm_ff14sb_tip3p_1ns-npt_50ns-nvt_interface-pbc_v2"
    materialize({"ingested": [_entry(release)]}, tmp_path, "fixture")
    state_entry = _entry(release)
    state_entry["already_complete"] = True
    state_entry["inserted_evaluation_count"] = 0
    state_entry["files"] = {"interface_analysis": state_entry["files"][0]}
    result = materialize({"ingested": [state_entry]}, tmp_path, "fixture")
    assert result["written_receipt_count"] == 0
    assert result["existing_idempotent_receipt_count"] == 1


def test_accepts_compact_launch_without_run_id_when_state_binds_run(tmp_path):
    _root(tmp_path)
    launch = tmp_path / "acea" / CANDIDATE / "launch_receipt.json"
    launch.write_text(
        json.dumps({"candidate_id": CANDIDATE, "target_key": "acea"}),
        encoding="utf-8",
    )
    result = materialize(
        {"ingested": [_entry(
            "openmm_ff14sb_tip3p_1ns-npt_50ns-nvt_interface-pbc_v2"
        )]}, tmp_path, "fixture")
    assert result["written_receipt_count"] == 1
