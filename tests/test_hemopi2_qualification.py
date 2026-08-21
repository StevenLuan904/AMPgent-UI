from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from pepagent.hemopi2_qualification import (
    ReferenceRecord,
    artifact_hashes,
    domain_features,
    frozen_calibration_ood_split,
    read_reference_dataset,
)


def _csv(rows: list[tuple[str, int]]) -> bytes:
    payload = "SEQUENCE,value,label\n" + "".join(
        f"{sequence},1.0,{label}\n" for sequence, label in rows
    )
    return payload.encode()


def test_reference_parser_pins_bytes_and_rejects_duplicates() -> None:
    payload = _csv([("ACDE", 0), ("KKLL", 1)])
    records = read_reference_dataset(
        payload, expected_sha256=hashlib.sha256(payload).hexdigest()
    )
    assert records == [ReferenceRecord("ACDE", 0), ReferenceRecord("KKLL", 1)]
    duplicate = _csv([("ACDE", 0), ("ACDE", 1)])
    with pytest.raises(ValueError, match="duplicate"):
        read_reference_dataset(
            duplicate, expected_sha256=hashlib.sha256(duplicate).hexdigest()
        )


def test_domain_features_are_label_blind() -> None:
    training = [ReferenceRecord("ACDEFG", 0), ReferenceRecord("KKLLWW", 1)]
    first = [ReferenceRecord("ACDEAA", 0), ReferenceRecord("RRRRRR", 1)]
    second = [ReferenceRecord(record.sequence, 1 - record.label) for record in first]
    np.testing.assert_allclose(
        domain_features(training, first), domain_features(training, second)
    )


def test_split_is_deterministic_disjoint_and_exhaustive() -> None:
    training = [ReferenceRecord("ACDEFG", 0), ReferenceRecord("KKLLWW", 1)]
    independent = [
        ReferenceRecord("ACDEAA", 0),
        ReferenceRecord("RRRRRR", 1),
        ReferenceRecord("VVVVVV", 0),
        ReferenceRecord("NNNNNN", 1),
        ReferenceRecord("GGGGGG", 0),
        ReferenceRecord("PPPPPP", 1),
        ReferenceRecord("YYYYYY", 0),
        ReferenceRecord("MMMMMM", 1),
    ]
    calibration, ood, _features = frozen_calibration_ood_split(training, independent)
    assert len(ood) == 2
    assert set(calibration).isdisjoint(ood)
    assert sorted([*calibration, *ood]) == list(range(len(independent)))
    assert (calibration, ood) == frozen_calibration_ood_split(training, independent)[:2]


def test_pinned_benchmark_artifacts_have_expected_canonical_hashes() -> None:
    root = Path(__file__).parents[1]
    artifacts = {
        "split": json.loads(
            (root / "config/enterprise/hemopi2_reference_split_v39.json").read_text(
                encoding="utf-8"
            )
        ),
        "calibration": json.loads(
            (
                root / "config/enterprise/hemopi2_calibration_threshold_v39.json"
            ).read_text(encoding="utf-8")
        ),
        "ood": json.loads(
            (root / "config/enterprise/hemopi2_ood_report_v39.json").read_text(
                encoding="utf-8"
            )
        ),
    }
    assert artifact_hashes(artifacts) == {
        "split": "dd939b33a855f422520ed7553e77331d42e7d1993e228a390fa5233ff2d47c46",
        "calibration": "d1eb08211496b4ed42c79b818f1fae29ba097b5e6df77792fb3d792f5976caf5",
        "ood": "ec134291a3f31e5db118e9ee1b23ebfb3023f859820fde0198258e110c56c196",
    }
