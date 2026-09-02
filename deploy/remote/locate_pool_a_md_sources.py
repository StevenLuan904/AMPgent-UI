"""Resolve unresolved Pool-A identities to remote Rosetta best-decoy paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--unresolved-state", type=Path, required=True)
    p.add_argument("--scan-root", type=Path, action="append", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    wanted = {
        (x["target_key"], x["sequence_sha256"]): x
        for x in json.loads(a.unresolved_state.read_text())["unresolved"]
    }
    found = {}
    for root in a.scan_root:
        for result in root.rglob("rosetta_result.json"):
            parts = result.parts
            try:
                i = parts.index("candidates")
                key = (parts[i + 1], parts[i + 2])
            except (ValueError, IndexError):
                continue
            if key not in wanted:
                continue
            data = json.loads(result.read_text())
            if not isinstance(data.get("best_decoy"), dict) or not data["best_decoy"].get(
                "structure"
            ):
                continue
            source = (
                result.parents[3] / "work" / "rosetta_coarse5" / data["best_decoy"]["structure"]
            )
            if source.exists() and (
                key not in found
                or source.stat().st_mtime > Path(found[key]["input_pdb"]).stat().st_mtime
            ):
                found[key] = {
                    **wanted[key],
                    "input_pdb": str(source),
                    "rosetta_result": str(result),
                    "structure_sha256": data["best_decoy"].get("structure_sha256"),
                }
    rows = list(found.values())
    payload = {
        "schema_version": "ampgent.pool-a-md-source-locations.1",
        "requested_count": len(wanted),
        "resolved_count": len(rows),
        "unresolved_count": len(wanted) - len(rows),
        "sources": rows,
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
