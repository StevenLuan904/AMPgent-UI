import pytest
from analysis.r129_postprocess_contract import apply_dual_support, select_one_per_cell, numeric_stats, validate_append_only_archive

def _row(aid="a", cell="q1", q="-1"):
    return {"action_id":aid,"candidate_id":aid,"cell_id":cell,"quality":q,"archive_status":"eligible","fixed_cell_selected":"False","ood_status":"unknown_not_assessed"}

def test_dual_requires_both_domains():
    with pytest.raises(ValueError, match="missing dual"):
        apply_dual_support([_row()], [{"action_id":"a","branch_key":"acea","activity_model_support_count_calibrated":"2"}])

def test_dual_rejects_duplicate_domain():
    cal=[{"action_id":"a","branch_key":"acea","activity_model_support_count_calibrated":"2"},{"action_id":"a","branch_key":"acea","activity_model_support_count_calibrated":"2"},{"action_id":"a","branch_key":"vegfa","activity_model_support_count_calibrated":"3"}]
    with pytest.raises(ValueError, match="duplicate"):
        apply_dual_support([_row()], cal)

def test_selection_clears_old_incumbent_and_keeps_one():
    prior=[dict(_row("old","q1","-1"),fixed_cell_selected="True")]
    out,summary=select_one_per_cell(prior,[_row("new","q1","-0.5")])
    assert [r["candidate_id"] for r in out if r["fixed_cell_selected"]=="True"]==["new"]
    assert summary["replacement_cells"]==["q1"]

def test_linear_quantiles_and_singleton():
    rows=[{"candidate_id":str(i),"x":str(i),"ood_status":"unknown_not_assessed"} for i in range(4)]
    s=numeric_stats(rows,{"x":("x","u","context_only")})["x"]
    assert s["median"]==1.5 and s["P25"]==.75 and s["P75"]==2.25
    one=numeric_stats([rows[0]],{"x":("x","u","context_only")})["x"]
    assert one["P10"]==one["P90"]==0.0

def test_missing_best_ood_and_nan_rejection():
    s=numeric_stats([{"candidate_id":"a","x":"1"},{"candidate_id":"b","x":""}],{"x":("x","u","min")})["x"]
    assert s["best_id"]=="a" and s["missing"]==1 and s["oodunknown"]==2
    with pytest.raises(ValueError, match="nonfinite"):
        select_one_per_cell([_row("bad","q1","NaN")],[])

def test_tie_preserves_existing_selected():
    prior=[dict(_row("old","q1","-1"),fixed_cell_selected="False"),dict(_row("incumbent","q1","-1"),fixed_cell_selected="True")]
    out,summary=select_one_per_cell(prior,[_row("new","q1","-1")])
    assert [r["candidate_id"] for r in out if r["fixed_cell_selected"]=="True"]==["incumbent"]
    assert summary["replacement_cells"]==[]


def _archive_fixture():
    previous = [{"candidate_id": "old", "target": "acea", "sequence": "AAA"}]
    raw = [
        {"candidate_id": "a1", "action_id": "a1", "target": "acea", "seed": "1", "conditional_nll": "1.25", "conditional_ppl": "3.5"},
        {"candidate_id": "v1", "action_id": "v1", "target": "vegfa", "seed": "2", "conditional_nll": "1.25", "conditional_ppl": "3.5"},
        {"candidate_id": "a2", "action_id": "a2", "target": "acea", "seed": "3", "conditional_nll": "2.25", "conditional_ppl": "4.5"},
        {"candidate_id": "v2", "action_id": "v2", "target": "vegfa", "seed": "4", "conditional_nll": "2.25", "conditional_ppl": "4.5"},
    ]
    expected = [{"action_id": r["action_id"], "target": r["target"], "seed": r["seed"], "conditional_nll": r["conditional_nll"], "conditional_ppl": r["conditional_ppl"]} for r in raw]
    aliases = [{"raw_candidate_id": "a1", "canonical_candidate_id": "a1", "duplicate_of": None}, {"raw_candidate_id": "v1", "canonical_candidate_id": "a1", "duplicate_of": "a1"}, {"raw_candidate_id": "a2", "canonical_candidate_id": "a2", "duplicate_of": None}, {"raw_candidate_id": "v2", "canonical_candidate_id": "a2", "duplicate_of": "a2"}]
    return previous, raw, expected, aliases


def test_append_archive_keeps_raw_alias_rows():
    previous, raw, expected, aliases = _archive_fixture()
    result = validate_append_only_archive(previous, raw, previous + raw, expected, aliases)
    assert result["raw_proposal_count"] == 4 and result["successor_count"] == 5


def test_append_archive_rejects_unique_only_successor():
    previous, raw, expected, aliases = _archive_fixture()
    with pytest.raises(ValueError, match="length"):
        validate_append_only_archive(previous, raw, previous + raw[:2], expected, aliases)


def test_append_archive_rejects_metadata_misjoin():
    previous, raw, expected, aliases = _archive_fixture()
    raw[1]["conditional_ppl"] = "99"
    with pytest.raises(ValueError, match="metric mismatch"):
        validate_append_only_archive(previous, raw, previous + raw, expected, aliases)
