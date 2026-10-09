"""Small, output-free postprocess contract for r133+.

The caller owns file I/O.  This module only derives fields from the actual
dual-domain calibration and validates append/provenance invariants.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from r129_postprocess_contract import apply_dual_support, validate_append_only_archive


def apply_dual_support_final_fields(rows: list[dict], calibration: list[dict]) -> list[dict]:
    """Attach both-domain support and overwrite stale raw-default fields.

    Formal12/display are kept separate from quality eligibility.  The latter
    additionally requires the frozen dual-domain support minimum >= 2.
    """
    out = apply_dual_support(rows, calibration)
    for row in out:
        # Never inherit candidate_scores' origin/default support fields.
        row["activity_model_support_count"] = str(row["dual_reference_support_min"])
        formal = str(row.get("formal12", row.get("formal_12_complete", ""))).lower() == "true"
        display = str(row.get("display_hard_gate", row.get("display_eligible", ""))).lower() == "true"
        support = int(row["dual_reference_support_min"])
        row["formal12"] = str(formal).lower()
        row["display_hard_gate"] = str(display).lower()
        row["excellent_sequence_stage"] = str(formal and display and support >= 2).lower()
        row["quality_eligible"] = str(formal and display and support >= 2).lower()
    return out


def summarize_gate_counts(rows: list[dict]) -> dict:
    """Return independent formal/display/quality denominators."""
    def yes(r, k): return str(r.get(k, "")).lower() == "true"
    return {
        "raw_rows": len(rows),
        "formal12": sum(yes(r, "formal12") for r in rows),
        "display": sum(yes(r, "display_hard_gate") for r in rows),
        "quality_eligible": sum(yes(r, "quality_eligible") for r in rows),
        "support_min_distribution": dict(Counter(str(r.get("dual_reference_support_min", "")) for r in rows)),
    }


def assert_amp_parent_fields(rows: list[dict], *, allow_unassessed: bool = False) -> None:
    """Require complete AMP5 and parent displacement evidence.

    ``allow_unassessed`` is an explicit audit mode only; it never makes a row
    complete or eligible.  Missing/invalid values otherwise fail loudly.
    """
    required = [f"amplify_submodel_{i}_probability" for i in range(1, 6)]
    for row in rows:
        for key in required:
            raw = row.get(key)
            if allow_unassessed and str(raw).lower() in ("", "unknown", "unassessed", "not_assessed"):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise AssertionError(f"missing/non-numeric AMP field: {key}") from exc
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise AssertionError(f"invalid AMP probability: {key}={raw}")
        for key, expected in (("parent_delta_phi", ("charge_density", "hydrophobicity", "moment", "length")),
                             ("parent_delta_objectives", ("hemo", "instab", "quality", "toxin"))):
            raw = row.get(key)
            if allow_unassessed and str(raw).lower() in ("", "unknown", "unassessed", "not_assessed"):
                continue
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise AssertionError(f"invalid JSON field: {key}") from exc
            if not isinstance(parsed, dict) or any(k not in parsed for k in expected):
                raise AssertionError(f"incomplete displacement field: {key}")
            for component in expected:
                try:
                    value = float(parsed[component])
                except (TypeError, ValueError) as exc:
                    raise AssertionError(f"non-numeric displacement: {key}.{component}") from exc
                if not math.isfinite(value):
                    raise AssertionError(f"non-finite displacement: {key}.{component}")


def validate_raw_append(previous: list[dict], raw: list[dict], successor: list[dict], expected: list[dict]) -> dict:
    """Delegate strict append-only validation without collapsing raw aliases."""
    return validate_append_only_archive(previous, raw, successor, expected)
