from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def legacy_decoys(candidate: Path) -> list[dict[str, Any]] | None:
    result_path = candidate / "results" / "rosetta_result.json"
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text())
            decoys = result.get("decoys")
            if isinstance(decoys, list) and len(decoys) >= 5:
                return [dict(item) for item in decoys[:5]]
        except (OSError, json.JSONDecodeError):
            pass
    decoys = []
    work = candidate / "work" / "rosetta" / "decoys"
    for index in range(1, 6):
        metric = work / f"decoy_{index:04d}.json"
        structure = work / f"decoy_{index:04d}.pdb"
        if not metric.is_file() or not structure.is_file():
            return None
        try:
            item = json.loads(metric.read_text())
            if item.get("dG_separated") is None or item.get("reweighted_sc") is None:
                return None
            item.pop("_checkpoint_prepacked_sha256", None)
            item.setdefault("index", index)
            item.setdefault("structure", str(structure))
            item.setdefault("structure_sha256", sha256_file(structure))
            decoys.append(item)
        except (OSError, json.JSONDecodeError):
            return None
    return decoys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    with (args.root / "inputs" / "candidates.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    eligible = 0
    finalized = 0
    for row in rows:
        candidate = args.root / "candidates" / row["branch_key"] / row["sequence_sha256"]
        receipt = candidate / "protocols" / "coarse5" / "completion_receipt.json"
        if receipt.is_file():
            try:
                if json.loads(receipt.read_text()).get("status") == "succeeded":
                    finalized += 1
                    continue
            except (OSError, json.JSONDecodeError):
                pass
        decoys = legacy_decoys(candidate)
        if decoys is None:
            continue
        eligible += 1
        dgs = [float(item["dG_separated"]) for item in decoys]
        primary = float(statistics.median(dgs))
        output = candidate / "protocols" / "coarse5" / "results" / "rosetta_result.json"
        write_json(output, {
            "schema_version": "1.0", "adapter_version": "pepagent-pyrosetta-flexpepdock-v3",
            "engine": "PyRosetta/FlexPepDock+InterfaceAnalyzer", "score_function": "ref2015",
            "interface": "A_B", "nstruct": 5, "primary_dG_separated_reu": primary,
            "primary_aggregation": {"rank_metric": "reweighted_sc", "top_decoy_count": 5, "aggregation": "median"},
            "dG_separated_reu": {"count": 5.0, "minimum": min(dgs), "median": primary, "maximum": max(dgs)},
            "decoys": decoys, "reused_existing_decoys": True,
        })
        write_json(receipt, {
            "schema_version": "ampgent.autoresearch-rosetta-candidate-completion.1", "protocol_version": "coarse5-v1",
            "status": "succeeded", "completed_at": datetime.now(timezone.utc).isoformat(),
            "candidate_id": row["candidate_id"], "sequence_sha256": row["sequence_sha256"], "target_key": row["branch_key"],
            "nstruct": 5, "interface": "A_B", "primary_dG_separated_reu": primary,
            "minimum_dG_separated_reu": min(dgs), "primary_aggregation": "median_dG_separated_of_all_5_decoys",
            "result_relative_path": "results/rosetta_result.json", "result_sha256": sha256_file(output),
            "reused_decoy_count": 5, "new_decoy_count": 0, "md_started": False,
        })
        finalized += 1
    print(json.dumps({"eligible_existing_prefix5": eligible, "coarse5_receipts": finalized}, sort_keys=True))


if __name__ == "__main__":
    main()
