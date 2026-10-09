import pytest
from analysis.r129_postprocess_contract import apply_dual_support, select_one_per_cell, numeric_stats, synchronize_selected_outputs, validate_append_only_archive, compute_parent_deltas, join_exact_parent, validate_generation_pair

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

def test_sync_uses_archive_winner_when_batch_flag_is_stale():
    old = dict(_row("old", "q1", "-1.2"), fixed_cell_selected="True")
    new = dict(_row("new", "q1", "-1.0"), fixed_cell_selected="False", support_count="2", formal12="True", display_hard_gate="True")
    selected, _ = select_one_per_cell([old], [new])
    synced, summary = synchronize_selected_outputs([new], selected)
    assert synced[0]["fixed_cell_selected"] == "True"
    assert synced[0]["quality_eligible"] == "true"
    assert summary["support_count_distribution"] == {"2": 1}

def test_sync_preserves_parent_delta_semantics_when_child_q_is_lower():
    incumbent = dict(_row("incumbent", "q3-h1-m3", "-1.3036971581493937"), fixed_cell_selected="True")
    child = dict(_row("child", "q3-h1-m3", "-1.1608902215957642"), fixed_cell_selected="False", support_count="2", formal12="True", display_hard_gate="True", parent_cell_id="q3-h1-m2", parent_quality="-1.151755452156067", parent_delta_objectives='{"quality":-0.009134769439697266}')
    selected, summary = select_one_per_cell([incumbent], [child])
    synced, _ = synchronize_selected_outputs([child], selected)
    assert float(child["quality"]) < float(child["parent_quality"])
    assert float(child["quality"]) > float(incumbent["quality"])
    assert summary["replacement_cells"] == ["q3-h1-m3"]
    assert synced[0]["parent_delta_objectives"] == '{"quality":-0.009134769439697266}'
    assert synced[0]["fixed_cell_selected"] == "True"

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

def test_parent_delta_uses_child_and_exact_parent_not_inherited_metadata():
    parent={"charge_density":"1","hydrophobicity":".2","moment":".3","length":"21","quality":"-1.2","macrel_hemolysis_probability":".2","toxinpred3_hybrid_score":".1","guruprasad_instability_index":"10"}
    child={"charge_density":".9","hydrophobicity":".2","moment":".4","length":"21","quality":"-1.1","macrel_hemolysis_probability":".1","toxinpred3_hybrid_score":".1","guruprasad_instability_index":"10","parent_delta_objectives":"{\"quality\":-99}"}
    d=compute_parent_deltas(child,parent)
    assert d["parent_delta_phi"]["charge_density"] == pytest.approx(-.1)
    assert d["parent_delta_objectives"]["quality"] == pytest.approx(.1)
    assert d["parent_domination_status"] == "child_dominates_parent"

def test_exact_parent_join_rejects_same_sequence_wrong_id_and_missing():
    rows=[{"candidate_id":"right","sequence":"SEQ"},{"candidate_id":"wrong","sequence":"SEQ"}]
    assert join_exact_parent(rows,"right","SEQ")["candidate_id"] == "right"
    assert join_exact_parent(rows,"missing","SEQ") is None

def test_parent_delta_classifies_tradeoff_equal_and_nonfinite():
    p={"charge_density":"1","hydrophobicity":".2","moment":".3","length":"21","quality":"-1","macrel_hemolysis_probability":".2","toxinpred3_hybrid_score":".1","guruprasad_instability_index":"10"}
    equal=dict(p)
    assert compute_parent_deltas(equal,p)["parent_domination_status"] == "equal"
    trade=dict(p, quality="-0.9", macrel_hemolysis_probability=".3")
    assert compute_parent_deltas(trade,p)["parent_domination_status"] == "tradeoff"
    nonfinite=dict(p, quality="nan")
    assert compute_parent_deltas(nonfinite,p)["parent_domination_status"] == "unknown_nonfinite"

def test_generation_pair_contract_rejects_off_by_one():
    validate_generation_pair(9, 10)
    with pytest.raises(ValueError, match="not parent generation"):
        validate_generation_pair(9, 11)


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


def test_append_archive_allows_derived_selection_flag_recompute():
    previous, raw, expected, aliases = _archive_fixture()
    previous[0]["fixed_cell_selected"] = "True"
    successor_old = dict(previous[0], fixed_cell_selected="False")
    raw[0]["fixed_cell_selected"] = "False"
    successor_new = dict(raw[0], fixed_cell_selected="True")
    result = validate_append_only_archive(previous, raw, [successor_old, successor_new] + raw[1:], expected, aliases)
    assert result["successor_count"] == 5


def test_append_archive_rejects_old_sequence_or_score_change():
    previous, raw, expected, aliases = _archive_fixture()
    changed = dict(previous[0], sequence="BBB", quality="-99")
    with pytest.raises(ValueError, match="changed"):
        validate_append_only_archive(previous, raw, [changed] + raw, expected, aliases)


def test_append_archive_selection_helper_replacement_snapshot():
    previous, raw, expected, aliases = _archive_fixture()
    previous[0].update(cell_id="q1", quality="-1", archive_status="eligible", fixed_cell_selected="True")
    raw[0].update(cell_id="q1", quality="-0.5", archive_status="eligible", fixed_cell_selected="False")
    selected, summary = select_one_per_cell(previous, [raw[0]])
    assert summary["replacement_cells"] == ["q1"]
    successor_old = dict(previous[0], fixed_cell_selected="False")
    successor_new = dict(raw[0], fixed_cell_selected="True")
    result = validate_append_only_archive(previous, raw, [successor_old, successor_new] + raw[1:], expected, aliases)
    assert result["successor_count"] == 5
