import pytest
from analysis.r129_postprocess_contract import apply_dual_support, select_one_per_cell, numeric_stats

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
