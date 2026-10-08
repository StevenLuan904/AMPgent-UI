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
