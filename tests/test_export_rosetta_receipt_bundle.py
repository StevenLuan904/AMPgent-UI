from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SOURCE_PATH = (
    Path(__file__).parents[1] / "deploy" / "remote" / "export_rosetta_receipt_bundle.py"
)
SPEC = importlib.util.spec_from_file_location("export_rosetta_receipt_bundle", SOURCE_PATH)
assert SPEC is not None and SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


def test_exporter_emits_only_succeeded_receipts_with_compact_scores(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidates" / "acea" / "candidate"
    result = candidate / "results" / "rosetta_result.json"
    result.parent.mkdir(parents=True)
    result.write_text('{"decoys":[]}', encoding="utf-8")
    receipt = candidate / "completion_receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": (
                    "ampgent.autoresearch-rosetta-candidate-completion.1"
                ),
                "status": "succeeded",
            }
        ),
        encoding="utf-8",
    )

    items = list(EXPORTER.iter_bundle_items(tmp_path))

    assert len(items) == 1
    assert items[0]["receipt_path"] == str(receipt.resolve())
    assert items[0]["receipt_json"] == receipt.read_text(encoding="utf-8")
    assert items[0]["result_json"] == result.read_text(encoding="utf-8")


def test_exporter_skips_incomplete_receipt(tmp_path: Path) -> None:
    candidate = tmp_path / "candidates" / "acea" / "candidate"
    candidate.mkdir(parents=True)
    (candidate / "completion_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": (
                    "ampgent.autoresearch-rosetta-candidate-completion.1"
                ),
                "status": "failed",
            }
        ),
        encoding="utf-8",
    )

    assert list(EXPORTER.iter_bundle_items(tmp_path)) == []
