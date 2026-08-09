from __future__ import annotations

import csv
import hashlib
import io
import math
from pathlib import Path

V25_SHA256 = "fac36b6dbbf4c7525ab7982f054c3c3b02632e0760b938b137d719f1a22a7b12"
V27_SHA256 = "96f74a51074843b8abc2968bc031d2f54a03774d4bf903ccc58bf34c71650382"
ROW_COUNT = 300
OUTPUT_FIELDS = (
    "comparison",
    "endpoint_scope",
    "n",
    "both_risk",
    "left_only",
    "right_only",
    "neither",
    "agreement_rate",
    "spearman_rho",
    "interpretation",
)


def _load(path: Path, expected_sha256: str) -> list[dict[str, str]]:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError(f"input SHA-256 mismatch: {path.name}")
    with io.StringIO(payload.decode("utf-8-sig"), newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != ROW_COUNT:
        raise ValueError(f"input row count mismatch: {path.name}")
    return rows


def join_frozen_rows(
    v25_rows: list[dict[str, str]], v27_rows: list[dict[str, str]]
) -> list[tuple[dict[str, str], dict[str, str]]]:
    def key(row: dict[str, str]) -> tuple[str, str]:
        candidate_id = row.get("candidate_id", "").strip()
        sequence_sha = row.get("sequence_sha256", "").strip().lower()
        sequence = row.get("sequence", "").strip().upper()
        if not candidate_id or not sequence_sha or not sequence:
            raise ValueError("input row has an empty identity field")
        if hashlib.sha256(sequence.encode()).hexdigest() != sequence_sha:
            raise ValueError("input row sequence SHA-256 mismatch")
        return candidate_id, sequence_sha

    v25_keys = [key(row) for row in v25_rows]
    v27_keys = [key(row) for row in v27_rows]
    if len(set(v25_keys)) != ROW_COUNT or len(set(v27_keys)) != ROW_COUNT:
        raise ValueError("input join keys are not unique")
    v27_by_key = dict(zip(v27_keys, v27_rows, strict=True))
    if set(v25_keys) != set(v27_keys):
        raise ValueError("v25 and v27 join key sets differ")
    return [(row, v27_by_key[row_key]) for row, row_key in zip(v25_rows, v25_keys, strict=True)]


def _finite(row: dict[str, str], column: str) -> float:
    try:
        value = float(row[column])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"invalid numeric field: {column}") from error
    if not math.isfinite(value):
        raise ValueError(f"non-finite numeric field: {column}")
    return value


def _binary_label(row: dict[str, str], column: str, risk: str, nonrisk: str) -> bool:
    value = row.get(column, "")
    if value == risk:
        return True
    if value == nonrisk:
        return False
    raise ValueError(f"unknown label in {column}: {value!r}")


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average = (start + 1 + end) / 2.0
        for position in range(start, end):
            ranks[order[position]] = average
        start = end
    return ranks


def spearman_rho(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("Spearman inputs must have equal length of at least two")
    left_ranks = _average_ranks(left)
    right_ranks = _average_ranks(right)
    left_mean = sum(left_ranks) / len(left_ranks)
    right_mean = sum(right_ranks) / len(right_ranks)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left_ranks, right_ranks, strict=True)
    )
    left_scale = sum((value - left_mean) ** 2 for value in left_ranks)
    right_scale = sum((value - right_mean) ** 2 for value in right_ranks)
    denominator = math.sqrt(left_scale * right_scale)
    if denominator == 0.0:
        raise ValueError("Spearman input is constant")
    return numerator / denominator


def _categorical_row(
    name: str,
    scope: str,
    left: list[bool],
    right: list[bool],
    interpretation: str,
) -> dict[str, object]:
    both = sum(a and b for a, b in zip(left, right, strict=True))
    left_only = sum(a and not b for a, b in zip(left, right, strict=True))
    right_only = sum(not a and b for a, b in zip(left, right, strict=True))
    neither = len(left) - both - left_only - right_only
    return {
        "comparison": name,
        "endpoint_scope": scope,
        "n": len(left),
        "both_risk": both,
        "left_only": left_only,
        "right_only": right_only,
        "neither": neither,
        "agreement_rate": f"{(both + neither) / len(left):.12f}",
        "spearman_rho": "",
        "interpretation": interpretation,
    }


def _continuous_row(
    name: str,
    scope: str,
    left: list[float],
    right: list[float],
    interpretation: str,
) -> dict[str, object]:
    return {
        "comparison": name,
        "endpoint_scope": scope,
        "n": len(left),
        "both_risk": "",
        "left_only": "",
        "right_only": "",
        "neither": "",
        "agreement_rate": "",
        "spearman_rho": f"{spearman_rho(left, right):.12f}",
        "interpretation": interpretation,
    }


def audit_rows(joined: list[tuple[dict[str, str], dict[str, str]]]) -> list[dict[str, object]]:
    if len(joined) != ROW_COUNT:
        raise ValueError("joined row count mismatch")
    macrel = [_binary_label(a, "macrel_hemolysis_label", "high", "low") for a, _ in joined]
    toxin = [_binary_label(a, "toxinpred3_label", "Toxin", "Non-Toxin") for a, _ in joined]
    hemo_rf = [round(_finite(b, "hemopi2_classification_score"), 3) >= 0.45 for _, b in joined]
    hemo_hc50 = [_finite(b, "hemopi2_hc50_um") < 100.0 for _, b in joined]
    macrel_probability = [_finite(a, "macrel_hemolysis_probability") for a, _ in joined]
    toxin_score = [_finite(a, "toxinpred3_hybrid_score") for a, _ in joined]
    hemo_score = [_finite(b, "hemopi2_classification_score") for _, b in joined]
    negative_hc50 = [-_finite(b, "hemopi2_hc50_um") for _, b in joined]
    same = "same-endpoint soft-prediction concordance only"
    cross = "cross-endpoint descriptive co-risk only; not hemolysis agreement"
    return [
        _categorical_row("macrel_hemolysis_vs_hemopi2_rf", "same_endpoint", macrel, hemo_rf, same),
        _categorical_row(
            "macrel_hemolysis_vs_hemopi2_hc50", "same_endpoint", macrel, hemo_hc50, same
        ),
        _categorical_row("hemopi2_rf_vs_hemopi2_hc50", "same_system", hemo_rf, hemo_hc50, same),
        _continuous_row(
            "macrel_probability_vs_hemopi2_rf_score",
            "same_endpoint",
            macrel_probability,
            hemo_score,
            same,
        ),
        _continuous_row(
            "macrel_probability_vs_negative_hc50",
            "same_endpoint",
            macrel_probability,
            negative_hc50,
            same,
        ),
        _categorical_row(
            "toxinpred3_toxicity_vs_hemopi2_rf", "cross_endpoint", toxin, hemo_rf, cross
        ),
        _categorical_row(
            "toxinpred3_toxicity_vs_hemopi2_hc50", "cross_endpoint", toxin, hemo_hc50, cross
        ),
        _continuous_row(
            "toxinpred3_score_vs_hemopi2_rf_score", "cross_endpoint", toxin_score, hemo_score, cross
        ),
        _continuous_row(
            "toxinpred3_score_vs_negative_hc50", "cross_endpoint", toxin_score, negative_hc50, cross
        ),
    ]


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    manifest = root / "config/benchmarks/amp_designer_safety_concordance_v28.yaml"
    marker = "\nexecution_status: audit_authorized\n"
    if marker not in f"\n{manifest.read_text(encoding='utf-8')}":
        raise RuntimeError("v28 audit is not authorized by the current status")
    v25 = _load(root / "reports/amp_generator_v25_candidate_metrics_20260809.csv", V25_SHA256)
    v27 = _load(root / "reports/amp_designer_safety_validation_v27_20260809.csv", V27_SHA256)
    rows = audit_rows(join_frozen_rows(v25, v27))
    output = root / "reports/amp_designer_safety_concordance_v28_20260809.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"output_sha256={hashlib.sha256(output.read_bytes()).hexdigest()}")
    print(f"comparison_rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
