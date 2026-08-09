from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
V27_PATH = ROOT / "config/benchmarks/amp_designer_safety_validation_v27.yaml"
V26_PATH = ROOT / "config/benchmarks/amp_designer_safety_validation_v26.yaml"


def _manifest(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_v27_is_append_only_and_smoke_passed_without_formal_access() -> None:
    v26 = _manifest(V26_PATH)
    v27 = _manifest(V27_PATH)
    assert v26["execution_status"] == "smoke_failed_nondeterministic"
    assert v27["parent_validation_id"] == v26["validation_id"]
    assert v27["parent_status_required"] == v26["execution_status"]
    assert v27["parent_results_immutable"] is True
    assert v27["execution_status"] == "passed_ready"
    assert v27["nonformal_smoke"]["status"] == "passed"
    audit = v27["nonformal_smoke"]["completed_audit"]
    assert audit["attempt_count"] == 2
    assert audit["canonical_bytes_equal"] is True
    assert audit["first_output_sha256"] == audit["second_output_sha256"]
    assert audit["formal_cohort_accessed"] is False
    assert v27["formal_execution"]["status"] == "ready_not_run"


def test_v27_freezes_full_v25_cohort_without_selection() -> None:
    manifest = _manifest(V27_PATH)
    cohort = manifest["input_cohort"]
    assert cohort["row_count"] == 300
    assert cohort["sha256"] == (
        "fac36b6dbbf4c7525ab7982f054c3c3b02632e0760b938b137d719f1a22a7b12"
    )
    assert cohort["selection_forbidden"] is True
    assert cohort["formal_access_before_ready_forbidden"] is True


def test_v27_locks_archived_upstream_hc50_semantics() -> None:
    manifest = _manifest(V27_PATH)
    validator = manifest["validator"]
    contract = validator["upstream_hc50_contract"]
    assert validator["regression_source_member_sha256"] == (
        "9e6ec8f13d96af52d653a7a7244a33440e6fb865fddceab0bcca42cf459b4bd8"
    )
    assert validator["audited_lines_sha256"] == (
        "0475d0a80acc4bbc826f2093614dd8b9626d6ecf13ce4ac3df8aa0b7c9336509"
    )
    assert contract["transform"] == "exp(-raw_prediction)"
    assert contract["report_round_decimal_places"] == 3
    assert contract["unit"] == "micromolar"
    assert validator["raw_regressor_output_must_not_be_labeled_hc50_um"] is True


def test_v27_smoke_sequence_order_and_determinism_are_frozen() -> None:
    smoke = _manifest(V27_PATH)["nonformal_smoke"]
    sequences = [entry["sequence"] for entry in smoke["sequences"]]
    payload = (json.dumps(sequences, separators=(",", ":")) + "\n").encode()
    assert hashlib.sha256(payload).hexdigest() == smoke["ordered_sequence_list_sha256"]
    assert smoke["attempt_count"] == 2
    assert smoke["run_in_two_fresh_processes"] is True
    assert smoke["canonicalization"]["hc50_round_decimal_places"] == 3
    assert smoke["canonicalization"]["classification_score_extra_rounding"] == (
        "forbidden"
    )
    assert smoke["failure_action"] == "terminal_fail_closed_no_rerun"


def test_v27_requires_single_thread_fresh_process_before_imports() -> None:
    runtime = _manifest(V27_PATH)["runtime_determinism_contract"]
    assert runtime["fresh_isolated_process_required"] is True
    assert runtime["environment_must_be_set_before_numpy_or_sklearn_import"] is True
    assert runtime["threadpool_max_threads"] == 1
    assert set(runtime["environment"]) == {
        "PYTHONHASHSEED",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    }
    assert set(runtime["environment"].values()) == {"0", "1"}
