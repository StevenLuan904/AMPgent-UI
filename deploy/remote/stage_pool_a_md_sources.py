"""Stage exact cross-host Rosetta best decoys without deleting their source."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--locations", type=Path, required=True)
    p.add_argument("--staging-root", type=Path, required=True)
    a = p.parse_args()
    payload = json.loads(a.locations.read_text())
    rows = []
    for item in payload["sources"]:
        source = Path(item["input_pdb"])
        destination = (
            a.staging_root / item["target_key"] / item["sequence_sha256"] / "best_decoy.pdb"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(source, destination)
        rows.append({**item, "staged_relative_path": str(destination.relative_to(a.staging_root))})
    (a.staging_root / "source_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "ampgent.pool-a-md-staged-sources.1",
                "source_count": len(rows),
                "sources": rows,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
