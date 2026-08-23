import csv
import io
import uuid
from types import SimpleNamespace

from pepagent.reporting import (
    BULK_ROSETTA_CSV_COLUMNS,
    build_bulk_rosetta_rows,
    render_bulk_rosetta_csv,
)


def test_bulk_rosetta_csv_has_stable_protocol_columns() -> None:
    payload = render_bulk_rosetta_csv(
        [
            {
                "run_id": "run-1",
                "candidate_id": "candidate-1",
                "sequence": "KASVNVSPRA",
                "rosetta_dg_separated_reu": -5.0,
                "rosetta_nstruct": 8,
                "prepack": True,
                "pack_input": False,
                "pack_separated": False,
            }
        ]
    )
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    assert list(rows[0]) == BULK_ROSETTA_CSV_COLUMNS
    assert rows[0]["sequence"] == "KASVNVSPRA"
    assert rows[0]["rosetta_nstruct"] == "8"
    assert rows[0]["prepack"] == "True"
    assert rows[0]["pack_separated"] == "False"


def test_bulk_rows_preserve_run_provenance_and_protocol_identity() -> None:
    run_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    tool_call_id = uuid.uuid4()
    candidate = SimpleNamespace(
        id=candidate_id,
        run_id=run_id,
        sequence="KASVNVSPRA",
        generation=3,
    )
    evaluation = SimpleNamespace(
        candidate_id=candidate_id,
        metric_name="rosetta_dg_separated_reu",
        numeric_value=-5.0,
        text_value=None,
        raw_json={
            "nstruct": 8,
            "adapter_version": "pepagent-pyrosetta-flexpepdock-v3",
            "score_function": "ref2015",
            "prepacked_input_sha256": "a" * 64,
            "pack_input": False,
            "pack_separated": False,
        },
        tool_call_id=tool_call_id,
    )
    rows = build_bulk_rosetta_rows([candidate], [evaluation])
    assert rows[0]["run_id"] == str(run_id)
    assert rows[0]["bulk_status"] == "succeeded"
    assert rows[0]["rosetta_adapter_version"] == "pepagent-pyrosetta-flexpepdock-v3"


def test_bulk_rows_export_guruprasad_instability_and_short_peptide_ood() -> None:
    run_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    candidate = SimpleNamespace(
        id=candidate_id,
        run_id=run_id,
        sequence="KASVNVSPRA",
        generation=1,
    )
    instability = SimpleNamespace(
        candidate_id=candidate_id,
        metric_name="guruprasad_instability_index",
        numeric_value=45.4,
        text_value=None,
        raw_json={},
        tool_call_id=uuid.uuid4(),
        out_of_domain=True,
    )

    row = build_bulk_rosetta_rows([candidate], [instability])[0]

    assert row["guruprasad_instability_index"] == 45.4
    assert row["guruprasad_instability_out_of_domain"] is True
    assert "guruprasad_instability_index" in BULK_ROSETTA_CSV_COLUMNS
    assert "guruprasad_instability_out_of_domain" in BULK_ROSETTA_CSV_COLUMNS
