"""Bounded, output-free r129 postprocess contracts.

This module only validates/operates on caller-provided rows.  It does not
read a round directory, invoke models, or write science artifacts.
"""
from __future__ import annotations
import math
from collections import defaultdict
from statistics import mean, median, stdev


def validate_dual_calibration(rows: list[dict], action_ids: set[str]) -> dict[str, dict[str, dict]]:
    """Require exactly one frozen-calibration row for each action×domain."""
    expected = {(aid, domain) for aid in action_ids for domain in ("acea", "vegfa")}
    seen: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (str(row.get("action_id", "")), str(row.get("branch_key", "")).lower())
        if key not in expected:
            raise ValueError(f"unexpected calibration key: {key}")
        if key in seen:
            raise ValueError(f"duplicate calibration key: {key}")
        seen[key] = row
    missing = sorted(expected - set(seen))
    if missing:
        raise ValueError(f"missing dual calibration rows: {missing}")
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for (aid, domain), row in seen.items():
        grouped[aid][domain] = row
    return dict(grouped)


def apply_dual_support(rows: list[dict], calibration: list[dict]) -> list[dict]:
    """Attach both-domain support and min support without origin overwrites."""
    ids = {str(r["action_id"]) for r in rows}
    grouped = validate_dual_calibration(calibration, ids)
    out = []
    for row in rows:
        aid = str(row["action_id"])
        domains = grouped[aid]
        supports = {d: int(domains[d]["activity_model_support_count_calibrated"]) for d in ("acea", "vegfa")}
        x = dict(row)
        x["support_acea"] = supports["acea"]
        x["support_vegfa"] = supports["vegfa"]
        x["dual_reference_support_min"] = min(supports.values())
        x["support_count"] = min(supports.values())
        x["support_status"] = "support>=2" if min(supports.values()) >= 2 else "support<2"
        out.append(x)
    return out


def select_one_per_cell(prior: list[dict], batch: list[dict]) -> tuple[list[dict], dict]:
    """Recompute selected flags from actual eligible rows; exactly one/cell."""
    prior_selected = {str(x.get("candidate_id")) for x in prior if x.get("fixed_cell_selected") == "True"}
    rows = [dict(x, fixed_cell_selected="False") for x in prior + batch]
    eligible = [x for x in rows if str(x.get("archive_status", "")) == "eligible" and x.get("cell_id") not in (None, "", "unresolved")]
    champions: dict[str, dict] = {}
    for row in eligible:
        try: q = float(row["quality"])
        except (TypeError, ValueError): raise ValueError(f"nonfinite q: {row.get('candidate_id')}")
        if not math.isfinite(q): raise ValueError(f"nonfinite q: {row.get('candidate_id')}")
        old = champions.get(row["cell_id"])
        if old is None or q > float(old["quality"]) or (q == float(old["quality"]) and row.get("candidate_id") in prior_selected and old.get("candidate_id") not in prior_selected):
            champions[row["cell_id"]] = row
    for row in rows:
        if row.get("cell_id") in champions and row.get("candidate_id") == champions[row["cell_id"]].get("candidate_id"):
            row["fixed_cell_selected"] = "True"
    counts = defaultdict(int)
    for row in rows:
        if row["fixed_cell_selected"] == "True": counts[row["cell_id"]] += 1
    if any(value != 1 for value in counts.values()):
        raise AssertionError(f"selected-per-cell violation: {dict(counts)}")
    before = {x["cell_id"]: x for x in prior if x.get("fixed_cell_selected") == "True"}
    after = {x["cell_id"]: x for x in rows if x.get("fixed_cell_selected") == "True"}
    new_cells = sorted(set(after) - set(before))
    replacements = sorted(cell for cell in set(after) & set(before) if after[cell]["candidate_id"] != before[cell]["candidate_id"])
    summary = {"selected_before": len(before), "selected_after": len(after), "new_cells": new_cells, "replacement_cells": replacements, "qd_sum_before": sum(float(x["quality"]) for x in before.values()), "qd_sum_after": sum(float(x["quality"]) for x in after.values())}
    return rows, summary


def numeric_stats(rows: list[dict], columns: dict[str, tuple[str, str, str]]) -> dict:
    """Dynamic all-row stats; eligibility is supplied by caller, never inferred as denominator."""
    out = {}
    for name, (field, unit, direction) in columns.items():
        observed = []
        for r in rows:
            try:
                v = float(r[field])
                if math.isfinite(v): observed.append((r, v))
            except (KeyError, TypeError, ValueError): pass
        vals = [v for _, v in observed]
        item = {"n": len(vals), "missing": len(rows) - len(vals), "failed": 0, "oodunknown": sum(str(r.get("ood_status") or "unknown_not_assessed") == "unknown_not_assessed" for r in rows), "unit": unit, "direction": direction}
        if vals:
            qs10 = sorted(vals)
            def quantile(p):
                if len(qs10)==1:return qs10[0]
                pos=p*(len(qs10)-1); lo=int(pos); hi=min(lo+1,len(qs10)-1); frac=pos-lo
                return qs10[lo]+frac*(qs10[hi]-qs10[lo])
            item.update(mean=mean(vals), std_ddof1=stdev(vals) if len(vals) > 1 else None, median=quantile(.5), min=min(vals), max=max(vals), P10=quantile(.1), P25=quantile(.25), P75=quantile(.75), P90=quantile(.9))
            if direction in ("min", "max"):
                best = min(vals) if direction == "min" else max(vals); worst = max(vals) if direction == "min" else min(vals)
                item["best_id"] = next(r["candidate_id"] for r,v in observed if v == best)
                item["worst_id"] = next(r["candidate_id"] for r,v in observed if v == worst)
        out[name] = item
    return out


def synchronize_selected_outputs(batch: list[dict], selected_archive: list[dict]) -> tuple[list[dict], dict]:
    """Project final selector flags/gates onto outputs by exact candidate ID."""
    by_id = {str(row.get("candidate_id")): row for row in selected_archive}
    if len(by_id) != len(selected_archive):
        raise ValueError("duplicate candidate_id in selected archive")
    out = []
    support_counts = defaultdict(int)
    for row in batch:
        cid = str(row.get("candidate_id"))
        archive_row = by_id.get(cid)
        if archive_row is None:
            raise ValueError(f"candidate missing from selected archive: {cid}")
        item = dict(row)
        item["fixed_cell_selected"] = archive_row.get("fixed_cell_selected", "False")
        support_raw = item.get("support_count", archive_row.get("support_count", ""))
        if support_raw in (None, ""):
            support_raw = item.get("dual_reference_support_min", archive_row.get("dual_reference_support_min", ""))
        try:
            support = int(float(support_raw))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"missing/non-numeric support_count: {cid}") from exc
        item["support_count"] = str(support)
        formal = str(item.get("formal12", item.get("formal_12_complete", ""))).lower() == "true"
        display = str(item.get("display_hard_gate", item.get("display_eligible", ""))).lower() == "true"
        cell = item.get("cell_id", archive_row.get("cell_id", ""))
        if str(item.get("archive_status", "")) in {"duplicate_alias", "historical_replay"}:
            item["quality_eligible"] = "False"
        else:
            item["quality_eligible"] = str(formal and display and support >= 2 and cell not in (None, "", "unresolved")).lower()
        support_counts[str(support)] += 1
        out.append(item)
    return out, {"support_count_distribution": dict(support_counts), "batch_count": len(out)}


def compute_parent_deltas(child: dict, parent: dict) -> dict:
    """Compute QD-axis and objective deltas from an exact joined parent row."""
    def delta(key):
        try:
            value = float(child[key]) - float(parent[key])
            return value if math.isfinite(value) else None
        except (KeyError, TypeError, ValueError):
            return None
    phi = {key: delta(key) for key in ("charge_density", "hydrophobicity", "moment", "length")}
    objectives = {key: delta(key) for key in ("quality", "macrel_hemolysis_probability", "toxinpred3_hybrid_score", "guruprasad_instability_index")}
    if not all(v is not None and math.isfinite(v) for v in (*phi.values(), *objectives.values())):
        status = "unknown_nonfinite"
    elif all(v == 0 for v in objectives.values()):
        status = "equal"
    elif objectives["quality"] >= 0 and all(objectives[k] <= 0 for k in objectives if k != "quality") and any(v != 0 for v in objectives.values()):
        status = "child_dominates_parent"
    elif objectives["quality"] <= 0 and all(objectives[k] >= 0 for k in objectives if k != "quality") and any(v != 0 for v in objectives.values()):
        status = "parent_dominates_child"
    else:
        status = "tradeoff"
    return {"parent_delta_phi": phi, "parent_delta_objectives": objectives, "parent_domination_status": status}


def join_exact_parent(parent_rows: list[dict], parent_label: str, parent_sequence: str) -> dict | None:
    """Resolve a parent only when both authoritative label and sequence match."""
    matches = [r for r in parent_rows if str(r.get("sequence", "")) == str(parent_sequence) and str(r.get("candidate_id", "")) == str(parent_label)]
    if len(matches) > 1:
        raise ValueError(f"duplicate exact parent identity: {parent_label}/{parent_sequence}")
    return matches[0] if matches else None


def validate_generation_pair(parent_generation: str | int, child_generation: str | int) -> None:
    """Require a masked child to be exactly one generation after its parent."""
    try:
        parent, child = int(parent_generation), int(child_generation)
    except (TypeError, ValueError) as exc:
        raise ValueError("generation must be integer-like") from exc
    if child != parent + 1:
        raise ValueError(f"child generation {child} is not parent generation {parent}+1")


def validate_append_only_archive(
    previous_rows: list[dict],
    raw_proposals: list[dict],
    successor_rows: list[dict],
    expected_actions: list[dict],
    aliases: list[dict] | None = None,
) -> dict:
    """Validate an archive append without collapsing raw aliases.

    ``successor_rows`` must contain every previous row followed by every raw
    proposal.  The four proposal identities are checked against the frozen
    action metadata, including source NLL/PPL; alias declarations are checked
    against the raw proposal IDs and must not silently create extra rows.
    """
    if len(successor_rows) != len(previous_rows) + len(raw_proposals):
        raise ValueError("append-only archive length must be previous + raw proposals")

    def stable_row(row: dict) -> dict:
        # Selection flags are a derived snapshot and may be recomputed when a
        # new candidate replaces an incumbent; all scientific/provenance fields
        # remain append-only and therefore compare strictly.
        return {key: value for key, value in row.items() if key != "fixed_cell_selected"}

    old_snapshot = successor_rows[: len(previous_rows)]
    if [stable_row(row) for row in old_snapshot] != [stable_row(row) for row in previous_rows]:
        raise ValueError("append-only archive changed or dropped previous rows")
    if [stable_row(row) for row in successor_rows[len(previous_rows) :]] != [stable_row(row) for row in raw_proposals]:
        raise ValueError("append-only archive does not append all raw proposals")

    expected = {str(row["action_id"]): row for row in expected_actions}
    actual = {str(row.get("action_id", row.get("candidate_id", ""))): row for row in raw_proposals}
    if len(actual) != len(raw_proposals):
        raise ValueError("raw proposals contain duplicate action IDs")
    if set(actual) != set(expected):
        raise ValueError("raw proposal action IDs do not match frozen actions")
    for action_id, frozen in expected.items():
        row = actual[action_id]
        for key in ("target", "seed"):
            if str(row.get(key)) != str(frozen.get(key)):
                raise ValueError(f"raw proposal metadata mismatch: {action_id}:{key}")
        for key in ("conditional_nll", "conditional_ppl"):
            try:
                if not math.isfinite(float(row[key])) or not math.isclose(float(row[key]), float(frozen[key]), rel_tol=1e-6, abs_tol=1e-8):
                    raise ValueError(f"raw proposal metric mismatch: {action_id}:{key}")
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, ValueError) and str(exc).startswith("raw proposal metric mismatch"):
                    raise
                raise ValueError(f"raw proposal metric missing/nonfinite: {action_id}:{key}") from exc

    if aliases is not None:
        raw_ids = set(actual)
        for alias in aliases:
            raw_id = str(alias.get("raw_candidate_id", ""))
            canonical_id = str(alias.get("canonical_candidate_id", ""))
            if raw_id not in raw_ids or canonical_id not in raw_ids:
                raise ValueError("alias references a non-raw candidate")
            if raw_id == canonical_id and alias.get("duplicate_of") not in (None, "", "null"):
                raise ValueError("canonical alias cannot have duplicate_of")
            if raw_id != canonical_id and str(alias.get("duplicate_of", "")) != canonical_id:
                raise ValueError("alias duplicate_of does not name its canonical raw row")
    return {
        "previous_count": len(previous_rows),
        "raw_proposal_count": len(raw_proposals),
        "successor_count": len(successor_rows),
        "raw_action_ids": sorted(actual),
        "alias_count": len(aliases or []),
    }
