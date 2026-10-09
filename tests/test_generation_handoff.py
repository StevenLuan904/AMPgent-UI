import csv
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "analysis" / "build_generation_handoff.py"


def _fixture(d: Path, round_number: int) -> None:
    for target, seed, seq in [("acea", 1, "AAAA"), ("vegfa", 2, "BBBB")]:
        action = f"r{round_number}-{target}-action"
        request = {"action_plans": [{"action_id": action, "parent_authoritative_candidate_id": "parent",
                                    "parent_lineage_generation": 3, "lineage_generation": 4}]}
        output = {"revision": "rev", "device": "cpu", "candidates": [{
            "action_id": action, "action_sha256": "a" * 64, "sequence": seq,
            "parent_sequence": "PPPP", "mutation_positions": [1], "seed": seed,
            "action_seed": seed, "conditional_nll": 1.0, "conditional_ppl": 2.0
        }, {
            "action_id": action + "-dup", "action_sha256": "b" * 64, "sequence": seq,
            "parent_sequence": "PPPP", "mutation_positions": [1], "seed": seed + 10,
            "action_seed": seed + 10, "conditional_nll": 1.1, "conditional_ppl": 2.1
        }]}
        request["action_plans"].append({**request["action_plans"][0], "action_id": action + "-dup"})
        (d / f"r{round_number}_{target}_request.json").write_text(json.dumps(request), encoding="utf-8")
        (d / f"r{round_number}_{target}_output.json").write_text(json.dumps(output), encoding="utf-8")
        (d / f"r{round_number}_{target}_claim.json").write_text(json.dumps({"proposal_round": round_number,
            "target": target, "status": "completed", "exit_code": 0}), encoding="utf-8")
    (d / "archive.csv").write_text("sequence\nOLD\n", encoding="utf-8")


def _run(d: Path, round_number: int) -> None:
    subprocess.run([sys.executable, str(SCRIPT), "--round-dir", str(d), "--archive", str(d / "archive.csv"),
                    "--round", str(round_number)], check=True, capture_output=True, text=True)


def test_round_parameter_and_raw_unique_split(tmp_path: Path):
    d = tmp_path / "r140"
    d.mkdir(); _fixture(d, 140); _run(d, 140)
    assert (d / "r140_generated_raw_occurrences.csv").exists()
    assert (d / "r140_generated_score_input.csv").exists()
    receipt = json.loads((d / "r140_generation_terminal_receipt.json").read_text())
    assert receipt["round"] == 140
    assert sum(1 for _ in csv.DictReader((d / "r140_generated_raw_occurrences.csv").open())) == 4
    assert sum(1 for _ in csv.DictReader((d / "r140_generated_score_input.csv").open())) == 2


def test_different_rounds_do_not_cross_name(tmp_path: Path):
    for n in (139, 140):
        d = tmp_path / f"r{n}"; d.mkdir(); _fixture(d, n); _run(d, n)
        assert not (d / f"r{141-n}_generation_terminal_receipt.json").exists()


def test_generic_launcher_is_round_parameterized():
    launcher = (Path(__file__).parents[1] / "analysis" / "launch_masked_round_cpu_validated.sh").read_text()
    assert 'ROUND="${ROUND:?}"' in launcher
    assert 'EXPECTED_ROUND="${EXPECTED_ROUND:?}"' in launcher
    assert 'r${ROUND}_${target}_request.json' in launcher
    assert 'round${ROUND}.lock' in launcher
    assert 'r139' not in launcher and 'r140' not in launcher
