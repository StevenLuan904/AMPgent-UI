from __future__ import annotations

import hashlib
import io
import pickle
from pathlib import Path

import numpy as np
import pytest
import yaml

from pepagent.hemopi2_adapter import (
    HEMOPI2_PICKLE_GLOBALS,
    FeatureMatrixContract,
    RestrictedSklearnUnpickler,
    assemble_feature_matrices,
    canonical_smoke_bytes,
    compute_basic_feature_block,
    compute_conjoint_triad_block,
    compute_ctd_block,
    compute_entropy_feature_block,
    compute_reference_table_feature_block,
    load_restricted_sklearn_pickle,
    validate_sequences,
)

ROOT = Path(__file__).parents[1]
HEMOPI2_DATA = (
    ROOT
    / "var/external-models/hemopi2/zenodo-14676712/rf-only-extracted-v1/Model/Data"
)


def _contract(names: list[str]) -> FeatureMatrixContract:
    payload = ("\n".join(names) + "\n").encode()
    return FeatureMatrixContract(
        feature_count=len(names),
        ordered_feature_names_sha256=hashlib.sha256(payload).hexdigest(),
        first_feature=names[0],
        last_feature=names[-1],
    )


def test_sequence_validation_is_strict_and_canonical() -> None:
    assert validate_sequences([" acd ", "WYK"]) == ["ACD", "WYK"]
    with pytest.raises(ValueError, match="invalid residues"):
        validate_sequences(["ACX"])
    with pytest.raises(ValueError, match="empty"):
        validate_sequences([" "])


def test_basic_feature_block_matches_audited_reference_arithmetic() -> None:
    values, names = compute_basic_feature_block(["AC"])
    assert values.shape == (1, 422)
    assert names[:4] == [
        "Molecular Weight (kDa)",
        "length",
        "AAC_A",
        "AAC_C",
    ]
    assert names[-1] == "DPC1_YY"
    assert values[0, 0] == pytest.approx(0.192)
    assert values[0, 1] == 2.0
    assert values[0, names.index("AAC_A")] == 50.0
    assert values[0, names.index("AAC_C")] == 50.0
    assert values[0, names.index("DPC1_AC")] == 100.0
    assert np.count_nonzero(values[0, 22:]) == 1
    assert hashlib.sha256(("\n".join(names) + "\n").encode()).hexdigest() == (
        "61bf86021298b4314b9471ed89e6e6c049fd8bebb18a98359ee200cdede9bf0c"
    )


def test_basic_feature_block_rejects_sequence_too_short_for_dpc() -> None:
    with pytest.raises(ValueError, match="DPC1"):
        compute_basic_feature_block(["A"])


def test_entropy_feature_block_matches_reference_sign_and_rounding() -> None:
    values, names = compute_entropy_feature_block(["AC"])
    assert values.shape == (1, 21)
    assert names[0] == "SER_A"
    assert names[-1] == "SEP"
    assert values[0, names.index("SER_A")] == -0.5
    assert values[0, names.index("SER_C")] == -0.5
    assert values[0, -1] == 1.0
    assert hashlib.sha256(("\n".join(names) + "\n").encode()).hexdigest() == (
        "07b18ea0c6d9f5e8d3ad0a55883b65959f0f1c74b56f41fc10e9075fe9a729c7"
    )


def test_reference_table_feature_block_matches_model_column_contract() -> None:
    values, names = compute_reference_table_feature_block(
        ["ACDEFGHIKLMNPQRSTVWY"], HEMOPI2_DATA
    )
    assert values.shape == (1, 104)
    assert np.isfinite(values).all()
    assert names[0] == "ATC_C"
    assert names[-1] == "DDR_Y"
    assert names[5:9] == ["BTC_T", "BTC_H", "BTC_S", "BTC_D"]
    assert values[0, :9].tolist() == pytest.approx(
        [27.72, 51.55, 7.51, 12.69, 0.52, 382.0, 202.0, 344.0, 38.0]
    )
    assert hashlib.sha256(("\n".join(names) + "\n").encode()).hexdigest() == (
        "3b4067038d952bc7831fb866c4fc31d6c5d4639150eab2b98501f744ab253c96"
    )


def test_reference_table_feature_block_is_repeatable_for_fixed_order() -> None:
    sequences = ["ACDEFG", "KKLLWW"]
    first = compute_reference_table_feature_block(sequences, HEMOPI2_DATA)
    second = compute_reference_table_feature_block(sequences, HEMOPI2_DATA)
    assert first[1] == second[1]
    assert np.array_equal(first[0], second[0])


def test_conjoint_triad_block_has_exact_model_order() -> None:
    values, names = compute_conjoint_triad_block(["AAA"])
    assert values.shape == (1, 343)
    assert names[0] == "CTC_111"
    assert names[-1] == "CTC_777"
    assert values[0, 0] == 1.0
    assert values.sum() == 1.0
    assert hashlib.sha256(("\n".join(names) + "\n").encode()).hexdigest() == (
        "1d7c07d7e24e40e0c4ace24a8fbf246cbe30f7fb43f86ec9650e04f7437c81ef"
    )


def test_ctd_block_preserves_reference_headers_and_finite_values() -> None:
    values, names = compute_ctd_block(
        ["ACDEFGHIKLMNPQRSTVWY"], HEMOPI2_DATA / "aa_attr_group.csv"
    )
    assert values.shape == (1, 189)
    assert np.isfinite(values).all()
    assert names[0] == "CeTD_HB1"
    assert names[-1] == "CeTD_100_p_SA3"
    assert values.sum() == pytest.approx(1159.0)
    assert hashlib.sha256(("\n".join(names) + "\n").encode()).hexdigest() == (
        "b989be84be86fc82d3b395d5887c264fa5db8b270b8660d91fe16cc1eec9e0d4"
    )


def test_complete_feature_matrices_close_both_model_contracts() -> None:
    sequences = ["ACDEFGHIKLMNPQRSTVWY", "KKLLWWRRAACCDD"]
    first = assemble_feature_matrices(sequences, HEMOPI2_DATA)
    second = assemble_feature_matrices(sequences, HEMOPI2_DATA)
    classifier, classifier_names = first["classification"]
    regression, regression_names = first["regression"]
    assert classifier.shape == (2, 1190)
    assert regression.shape == (2, 1167)
    assert np.isfinite(classifier).all()
    assert np.isfinite(regression).all()
    assert classifier_names[-1] == "SEP_LR"
    assert regression_names[-1] == "SOC1_G1"
    assert np.array_equal(classifier, second["classification"][0])
    assert np.array_equal(regression, second["regression"][0])
    assert hashlib.sha256(classifier.tobytes()).hexdigest() == (
        "111cdda9288ca190992d04df8437c49e457c828fde1bfd0fe091a1d75a900f9e"
    )
    assert hashlib.sha256(regression.tobytes()).hexdigest() == (
        "299640291677ebe02e2141cfe328dd92b4bdddfa4bdf55010a38cea4198c20c6"
    )


def test_feature_contract_accepts_only_exact_finite_matrix() -> None:
    names = ["first", "middle", "last"]
    contract = _contract(names)
    contract.validate(np.asarray([[1.0, 2.0, 3.0]]), names)
    with pytest.raises(ValueError, match="non-finite"):
        contract.validate(np.asarray([[1.0, np.nan, 3.0]]), names)
    with pytest.raises(ValueError, match="digest"):
        contract.validate(
            np.asarray([[1.0, 2.0, 3.0]]), ["first", "changed", "last"]
        )


def test_restricted_unpickler_rejects_unapproved_global() -> None:
    payload = pickle.dumps(Path("forbidden"), protocol=4)
    with pytest.raises(pickle.UnpicklingError, match="outside the allowlist"):
        RestrictedSklearnUnpickler(
            stream := io.BytesIO(payload), allowed_globals=frozenset()
        ).load()
    stream.close()


def test_model_loader_checks_digest_before_deserialization(tmp_path: Path) -> None:
    path = tmp_path / "model.sav"
    path.write_bytes(pickle.dumps({"safe": "data"}, protocol=4))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_restricted_sklearn_pickle(path, expected_sha256="0" * 64)


def test_adapter_allowlist_matches_preregistered_contract() -> None:
    manifest = yaml.safe_load(
        (ROOT / "config/benchmarks/amp_designer_safety_validation_v26.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert set(manifest["adapter_contract"]["pickle_global_allowlist"]) == set(
        HEMOPI2_PICKLE_GLOBALS
    )


def test_adapter_source_has_no_execution_or_network_surface() -> None:
    source = (ROOT / "src/pepagent/hemopi2_adapter.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "socket", "requests", "urllib")
    assert not any(token in source for token in forbidden)


def test_canonical_smoke_bytes_are_order_stable_and_reject_nan() -> None:
    records = [{"sequence": "ACD", "score": 0.25}]
    assert canonical_smoke_bytes(records) == canonical_smoke_bytes(records)
    with pytest.raises(ValueError):
        canonical_smoke_bytes([{"score": float("nan")}])
