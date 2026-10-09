"""Read-only B30 successor selector using the frozen B93 contract."""
from __future__ import annotations
import argparse, csv, hashlib, json, math
from pathlib import Path
from typing import Any, Iterable

AXES = ("quality", "macrel_hemolysis_probability", "toxinpred3_hybrid_score", "guruprasad_instability_index")
MIN_SUPPORT, MAX_B = 2, 30
_DIR = {"quality": 1, "macrel_hemolysis_probability": -1, "toxinpred3_hybrid_score": -1, "guruprasad_instability_index": -1}

def _sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def _truth(value: Any) -> bool: return str(value).strip().lower() in {"true", "1", "yes"}
def _num(row: dict[str, Any], key: str) -> float | None:
    try: value = float(row.get(key, ""))
    except (TypeError, ValueError): return None
    return value if math.isfinite(value) else None
def _quality(row: dict[str, Any]) -> float | None:
    explicit = _num(row, "quality")
    if explicit is not None: return explicit
    llamp, amp = _num(row, "llamp_log10_mic_um"), _num(row, "amp_read_log10_mic_um")
    return -max(llamp, amp) if llamp is not None and amp is not None else None
def _support(row: dict[str, Any]) -> int | None:
    for key in ("dual_reference_support_min", "support_count"):
        try: return int(float(row.get(key, "")))
        except (TypeError, ValueError): continue
    return None
def _key(row: dict[str, Any]) -> tuple[str, str]: return str(row.get("candidate_id", "")), str(row.get("sequence", ""))
def _close(a: float, b: float) -> bool: return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-8)

def gate(row: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not (_truth(row.get("formal12")) or (_truth(row.get("all_finite")) and str(row.get("formal_metric_count")) == "12")): reasons.append("formal12")
    instability = _num(row, "guruprasad_instability_index")
    if str(row.get("toxinpred3_label", "")).strip().lower() != "non-toxin" or str(row.get("macrel_hemolysis_label", "")).strip().lower() != "low" or instability is None or instability > 50: reasons.append("display")
    support = _support(row)
    if support is None or support < MIN_SUPPORT: reasons.append("dual_support")
    if _quality(row) is None: reasons.append("quality")
    hemo = _num(row, "macrel_hemolysis_probability")
    if hemo is None or hemo > .5 or str(row.get("macrel_hemolysis_label", "")).strip().lower() != "low": reasons.append("qd_quality")
    if not str(row.get("cell_id", "")).strip(): reasons.append("cell_missing")
    return not reasons, reasons

def _value(row: dict[str, Any], axis: str) -> float | None: return _quality(row) if axis == "quality" else _num(row, axis)
def comparison(x: dict[str, Any], y: dict[str, Any]) -> tuple[bool, str | None]:
    xv, yv = [], []
    for axis in AXES:
        a, b = _value(x, axis), _value(y, axis)
        if a is None or b is None: return False, f"comparison_pending:{axis}"
        xv.append(_DIR[axis]*a); yv.append(_DIR[axis]*b)
    return all(a >= b or _close(a,b) for a,b in zip(xv,yv)) and any(a > b and not _close(a,b) for a,b in zip(xv,yv)), None
def dominates(x: dict[str, Any], y: dict[str, Any]) -> bool: return comparison(x,y)[0]
def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["_source_path"] = str(path)
    return rows
def _read_json_rows(path: Path) -> list[dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8")).get("entries", []))
def _enrich(rows: list[dict[str, Any]], evidence: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in evidence:
        if _key(item)[0]: by_key.setdefault(_key(item), []).append(item)
    enriched = []
    for row in rows:
        merged = dict(row)
        candidates = by_key.get(_key(row), [])
        vals = []
        for item in candidates:
            value = _num(item, "guruprasad_instability_index")
            if value is not None: vals.append((value, str(item.get("_source_path", ""))))
        distinct = []
        for value, source in vals:
            if not any(_close(value, old) for old, _ in distinct): distinct.append((value, source))
        existing = _num(row, "guruprasad_instability_index")
        if existing is None and len(distinct) == 1:
            merged["guruprasad_instability_index"] = str(distinct[0][0])
            merged["guruprasad_evidence_source"] = distinct[0][1]
        elif existing is None and len(distinct) > 1:
            merged["guruprasad_evidence_conflict"] = [{"value": value, "source": source} for value, source in distinct]
        enriched.append(merged)
    return enriched

def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}
def _identity_index(rows: Iterable[dict[str, Any]]) -> tuple[dict[tuple[str,str],dict[str,Any]], list[dict[str,Any]]]:
    index, by_id, conflicts = {}, {}, []
    for row in rows:
        key = _key(row)
        if not key[0]: continue
        by_id.setdefault(key[0], set()).add(key[1])
        if key in index:
            conflicts.append({"candidate_id": key[0], "sequence": key[1], "reason": "duplicate_exact_identity"})
            for axis in AXES:
                old, new = _value(index[key], axis), _value(row, axis)
                if old is not None and new is not None and not _close(old, new):
                    conflicts.append({"candidate_id": key[0], "sequence": key[1], "reason": "conflicting_scalar", "axis": axis, "old": old, "new": new})
        index[key] = row
    conflicts.extend({"candidate_id": cid, "sequences": sorted(seqs), "reason": "candidate_id_sequence_conflict"} for cid,seqs in by_id.items() if len(seqs)>1)
    return index, conflicts

def select(current: Iterable[dict[str, Any]], prior_b: list[dict[str, Any]], archive: Iterable[dict[str, Any]], protected_a_keys: set[tuple[str,str]] | None = None) -> dict[str, Any]:
    current_rows, archive_rows = list(current), list(archive); protected_a_keys = protected_a_keys or set()
    archive_index, archive_conflicts = _identity_index(archive_rows); _, current_conflicts = _identity_index(current_rows)
    if archive_conflicts or current_conflicts:
        return {"schema_version":"ampgent.dual-b-selection-dry-run.v2", "decision_status":"decision_not_ready", "reason":"identity_or_scalar_conflict", "archive_identity_conflicts":archive_conflicts, "candidate_identity_conflicts":current_conflicts, "selected_ids":[], "replacement_count":0}
    merged_current = _enrich(current_rows, archive_rows)
    prior_merged, missing_ids = [], []
    for old in prior_b:
        source = archive_index.get(_key(old))
        if source is None:
            missing_ids.append({"candidate_id": old.get("candidate_id", ""), "sequence": old.get("sequence", ""), "reason": "missing_exact_archive_identity"}); prior_merged.append(dict(old))
        else: prior_merged.append(_enrich([old], [source])[0])
    if len(prior_b) > MAX_B: raise ValueError(f"illegal prior B size {len(prior_b)} > {MAX_B}")
    prior_keys = {_key(x) for x in prior_merged}; prior_sequences = {str(x.get("sequence", "")) for x in prior_merged}; gated=[]; excluded=[]; seen=set(); seen_sequences=set()
    for row in merged_current:
        key = _key(row); ok, reasons = gate(row)
        if key in prior_keys: ok=False; reasons=[*reasons,"already_in_B"]
        elif key[1] in prior_sequences: ok=False; reasons=[*reasons,"sequence_already_in_B"]
        if key in seen or key[1] in seen_sequences: ok=False; reasons=[*reasons,"duplicate_sequence_in_pool"]
        seen.add(key); seen_sequences.add(key[1]); item={**row,"candidate_id":key[0],"sequence":key[1],"reasons":reasons}; (gated if ok else excluded).append(item)
    front=[]; pending=[]
    for row in gated:
        dominated=False
        for other in gated:
            if other is row: continue
            wins, reason=comparison(other,row)
            if reason: pending.append({"candidate_id":row["candidate_id"],"against":other["candidate_id"],"reason":reason})
            if wins: dominated=True; break
        if not dominated: front.append(row)
    selected=list(prior_merged); replacements=[]; used=set()
    for challenger in sorted(front,key=lambda r:(-float(_quality(r)),r["candidate_id"],r["sequence"])):
        options=[]
        for i,old in enumerate(selected):
            oldkey=_key(old)
            if oldkey in protected_a_keys: continue
            oldcell=str(old.get("cell_id","")); active=sum(1 for a in selected if str(a.get("cell_id",""))==oldcell and _key(a)!=oldkey)
            if not oldcell or active < 1: continue
            wins, reason=comparison(challenger,old)
            if reason: pending.append({"candidate_id":challenger["candidate_id"],"against":old["candidate_id"],"reason":reason})
            if wins: options.append((i,old,oldcell))
        if options and _key(challenger) not in used:
            i,old,cell=sorted(options,key=lambda z:(_quality(z[1]) if _quality(z[1]) is not None else 999,z[1].get("candidate_id","")))[0]
            selected[i]=challenger; used.add(_key(challenger)); replacements.append({"removed_candidate_id":old["candidate_id"],"removed_sequence":old["sequence"],"new_candidate_id":challenger["candidate_id"],"new_sequence":challenger["sequence"],"old_cell":cell,"old_axes":{axis:_value(old,axis) for axis in AXES},"new_axes":{axis:_value(challenger,axis) for axis in AXES},"remaining_old_cell_ids":[x["candidate_id"] for x in selected if str(x.get("cell_id",""))==cell]})
    selected = [_public(x) for x in selected]
    selected_keys=[_key(x) for x in selected]
    if len(selected)>MAX_B or len(set(selected_keys))!=len(selected_keys) or len({x[1] for x in selected_keys})!=len(selected_keys): raise ValueError("final B violates unique ID+sequence <=30")
    return {"schema_version":"ampgent.dual-b-selection-dry-run.v2","axes":AXES,"directions":{k:("max" if v==1 else "min") for k,v in _DIR.items()},"gate":"actual formal12 && toxin Non-Toxin && Macrel low && finite instability<=50 && dual_support>=2 && finite q && hemo_probability<=0.5","prior_b_count":len(prior_b),"max_b":MAX_B,"candidate_identity_conflicts":current_conflicts,"archive_identity_conflicts":archive_conflicts,"missing_exact_archive_ids":missing_ids,"gated_count":len(gated),"excluded_count":len(excluded),"excluded":[{"candidate_id":x["candidate_id"],"sequence":x["sequence"],"reasons":x["reasons"]} for x in excluded],"pareto_front_ids":sorted(x["candidate_id"] for x in front),"comparison_pending":pending,"protected_b_a_intersection_count":sum(_key(x) in protected_a_keys for x in prior_merged),"replacement_count":len(replacements),"replacements":replacements,"selected_ids":[x["candidate_id"] for x in selected],"selected_unique_count":len(set(selected_keys)),"selected_unique_sequence_count":len({x[1] for x in selected_keys}),"no_change_vs_prior":set(selected_keys)=={_key(x) for x in prior_merged},"_selected_entries":selected,"selection_semantics":"exact ID+sequence joins; frozen B roles retained; one challenger replaces at most one active B slot; old cell remains represented"}

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--current",type=Path,required=True); p.add_argument("--prior-b",type=Path,required=True); p.add_argument("--archive",type=Path,required=True); p.add_argument("--prior-a",type=Path); p.add_argument("--formal",type=Path,action="append",default=[]); p.add_argument("--min-round",type=int); p.add_argument("--max-round",type=int); p.add_argument("--proposal-round",type=int,help="round recorded in successor/receipt; defaults to the current input maximum"); p.add_argument("--output",type=Path,required=True); p.add_argument("--successor",type=Path); p.add_argument("--receipt",type=Path); a=p.parse_args()
    archive_rows=_read_csv(a.archive); evidence=[]; formal_paths=[]
    for path in a.formal:
        evidence.extend(_read_csv(path)); formal_paths.append(path)
    # Archive rows retain the historical formal source path; load it when available
    # so missing B-entry axes are never silently treated as non-dominance.
    pending_sources = [Path(str(row.get("source_file", ""))) for row in archive_rows if row.get("source_file")]
    seen_sources = set(formal_paths)
    while pending_sources:
        source = pending_sources.pop()
        if source in seen_sources or not source.exists():
            continue
        seen_sources.add(source); loaded = _read_csv(source); evidence.extend(loaded); formal_paths.append(source)
        pending_sources.extend(Path(str(row.get("source_file", ""))) for row in loaded if row.get("source_file"))
    current_rows=_enrich(_read_csv(a.current), evidence); archive_rows=_enrich(archive_rows, evidence)
    if a.min_round is not None or a.max_round is not None:
        current_rows=[row for row in current_rows if (a.min_round is None or int(row.get("proposal_round", 0) or 0) >= a.min_round) and (a.max_round is None or int(row.get("proposal_round", 0) or 0) <= a.max_round)]
    proposal_round = a.proposal_round
    if proposal_round is None:
        rounds = [int(row.get("proposal_round", 0) or 0) for row in current_rows if str(row.get("proposal_round", "")).strip()]
        proposal_round = max(rounds, default=0)
    arows=_read_json_rows(a.prior_a) if a.prior_a else []; result=select(current_rows,_read_json_rows(a.prior_b),archive_rows,{_key(x) for x in arows})
    paths=[("current",a.current),("prior_b",a.prior_b),("archive",a.archive)]+([("prior_a",a.prior_a)] if a.prior_a else [])+[(f"formal_{i}",x) for i,x in enumerate(formal_paths)]; result["inputs"]={k:{"path":str(v),"sha256":_sha(v)} for k,v in paths}
    selected_entries=result.pop("_selected_entries", [])
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    successor_path = getattr(a, "successor", None)
    receipt_path = getattr(a, "receipt", None)
    if successor_path:
        def repo_rel(path: Path | None) -> str | None:
            if path is None: return None
            value = str(path).replace("\\", "/")
            return value.split("agent-platform/", 1)[-1] if "agent-platform/" in value else value
        canonical = {str(x.get("sequence", "")): str(x.get("candidate_id", "")) for x in selected_entries}
        successor_path.write_text(json.dumps({"schema_version":"ampgent.dual-b-pool.successor.v2","campaign_id":"acea-vegfa-dual-autoresearch-20260923-v1","manifest_role":"persistent_B_pool","proposal_round":proposal_round,"successor_of":repo_rel(a.prior_b),"entry_count":len(selected_entries),"max_entries":MAX_B,"generic_amp_is_binding":False,"no_A_promotion":True,"replacement_rule":"nonweighted_four_axis_strict_dominance_old_cell_occurrence_ge_2_nonA14","current_A_pointer":repo_rel(a.prior_a),"canonical_representative_by_sequence":canonical,"entries":selected_entries,"selection_receipt":repo_rel(receipt_path),"source_metadata":result["inputs"]},ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    if receipt_path:
        receipt = {"schema_version":"ampgent.dual-b-selection.receipt.v1","proposal_round":proposal_round,"selection":result,"status":"decision_ready"}
        if proposal_round == 115:
            receipt["r115_parent_binding_correction"] = "reports/dual_qd_acea_vegfa_round115_rebuilt_corrected_20261009/round115_parent_binding_correction_appendonly.json"
            receipt["r115_parent_binding_sha256"] = "22a8094280fe6642ab1aa4ee82d7c7c3ea30f374bd9e1b4378df124c58b40e28"
        receipt_path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__ == "__main__": main()
