"""Fail-closed inventory for target-agnostic v8 source grafts.

This stage only selects eligible source donors. It never relaxes safety,
activity-support, or PostgreSQL exact-history gates and performs no GPU work.
"""

from __future__ import annotations

from collections.abc import Iterable


def _true(row: dict[str, str], key: str) -> bool:
    return row.get(key, "").strip().lower() in {"true", "1", "yes"}


def eligible_donor(row: dict[str, str]) -> bool:
    try:
        support = int(float(row.get("activity_model_support_count_calibrated", "0")))
        instability = float(row.get("guruprasad_instability_index", "inf"))
    except ValueError:
        return False
    return (
        _true(row, "display_eligible")
        and row.get("toxinpred3_label", "").lower() == "non-toxin"
        and row.get("macrel_hemolysis_label", "").lower() == "low"
        and instability <= 50
        and support >= 2
        and bool(row.get("sequence_sha256"))
    )


def select_source_donors(
    rows: Iterable[dict[str, str]],
    source_token: str,
    limit: int = 16,
    *,
    require_display: bool = True,
) -> list[dict[str, str]]:
    token = source_token.lower()
    selected = [
        row for row in rows
        if token in " ".join(
            row.get(key, "")
            for key in ("donor_source", "generator_tool", "source", "proposal_mode")
        ).lower()
        and (eligible_donor(row) if require_display else bool(row.get("sequence_sha256")))
    ]
    return sorted(
        selected,
        key=lambda row: (
            -int(float(row.get("activity_model_support_count_calibrated", 0))),
            row.get("sequence_sha256", ""),
        ),
    )[:limit]


def inventory(rows: Iterable[dict[str, str]]) -> dict[str, int]:
    material = list(rows)
    return {
        "input_count": len(material),
        "display_safe_support_ge_2_count": sum(eligible_donor(row) for row in material),
        "pepglad_count": len(select_source_donors(material, "pepglad", require_display=False)),
        "pepflow_count": len(select_source_donors(material, "pepflow")),
    }


def generate_children(
    parents: Iterable[dict[str, str]],
    donors: Iterable[dict[str, str]],
    source_token: str,
    limit: int = 16,
) -> list[dict[str, str]]:
    """Generate bounded equal-length 3-aa center grafts; no safety claim is made."""
    children: list[dict[str, str]] = []
    seen: set[str] = set()
    for parent in sorted(parents, key=lambda row: row.get("sequence_sha256", "")):
        sequence = parent.get("sequence", "").strip().upper()
        if len(sequence) < 6:
            continue
        center = (len(sequence) - 3) // 2
        for donor in donors:
            fragment = donor.get("sequence", "").strip().upper()[:3]
            if len(fragment) != 3:
                continue
            child = sequence[:center] + fragment + sequence[center + 3 :]
            digest = __import__("hashlib").sha256(child.encode()).hexdigest()
            if child == sequence or digest in seen:
                continue
            seen.add(digest)
            children.append({
                "sequence": child,
                "sequence_sha256": digest,
                "parent_candidate_id": parent.get("candidate_id", ""),
                "donor_candidate_id": donor.get("candidate_id", ""),
                "donor_source": source_token,
                "donor_display_eligible": str(eligible_donor(donor)).lower(),
                "graft_start_zero_based": str(center),
                "graft_length": "3",
            })
            if len(children) >= limit:
                return children
    return children
