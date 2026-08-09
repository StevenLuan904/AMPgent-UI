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
    canonical_smoke_bytes,
    load_restricted_sklearn_pickle,
    validate_sequences,
)

ROOT = Path(__file__).parents[1]


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
