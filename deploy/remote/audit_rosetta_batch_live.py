from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def decoy_count(candidate: Path) -> int:
    decoys = candidate / "work" / "rosetta" / "decoys"
    count = 0
    for metric in decoys.glob("decoy_*.json"):
        structure = metric.with_suffix(".pdb")
        if structure.is_file() and metric.stat().st_size and structure.stat().st_size:
            count += 1
    return count


def audit(root: Path, parent_pid: int) -> dict[str, object]:
    candidates = list(root.glob("candidates/*/*"))
    counts = [decoy_count(candidate) for candidate in candidates]
    completed = []
    coarse5 = []
    boltz = 0
    failed = 0
    coarse5_checkpoint_total = 0
    reused_hardlink_count = 0
    for candidate in candidates:
        receipt = candidate / "completion_receipt.json"
        if receipt.is_file():
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            if payload.get("status") == "succeeded":
                completed.append(int(payload.get("nstruct", 0)))
            elif payload.get("status") == "failed":
                failed += 1
        receipt5 = candidate / "protocols" / "coarse5" / "completion_receipt.json"
        if receipt5.is_file():
            payload5 = json.loads(receipt5.read_text(encoding="utf-8"))
            if payload5.get("status") == "succeeded":
                coarse5.append(int(payload5.get("nstruct", 0)))
        if (candidate / "inputs" / "boltz_model_0.pdb").is_file():
            boltz += 1
        for index in range(1, 6):
            old = candidate / "work" / "rosetta" / "decoys" / f"decoy_{index:04d}.json"
            new = candidate / "work" / "rosetta_coarse5" / "decoys" / f"decoy_{index:04d}.json"
            if new.is_file():
                coarse5_checkpoint_total += 1
            if old.is_file() and new.is_file() and old.stat().st_ino == new.stat().st_ino:
                reused_hardlink_count += 1
    process_alive = False
    command = None
    proc = Path(f"/proc/{parent_pid}")
    if proc.exists():
        process_alive = True
        try:
            command = proc.joinpath("cmdline").read_bytes().replace(b"\0", b" ").decode()
        except (OSError, UnicodeDecodeError):
            command = "unreadable"
    histogram = Counter(counts)
    return {
        "schema_version": "ampgent.rosetta-live-audit.1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "host": os.uname().nodename,
        "root": str(root.resolve()),
        "parent_pid": parent_pid,
        "parent_alive": process_alive,
        "parent_command": command,
        "candidate_directories": len(candidates),
        "boltz_coordinate_count": boltz,
        "legacy_completion_count": len(completed),
        "legacy_completion_nstruct": dict(sorted(Counter(completed).items())),
        "coarse5_completion_count": len(coarse5),
        "coarse5_checkpoint_total": coarse5_checkpoint_total,
        "reused_legacy_checkpoint_hardlinks": reused_hardlink_count,
        "failed_count": failed,
        "decoy_checkpoint_total": sum(counts),
        "candidate_decoy_histogram": {str(k): v for k, v in sorted(histogram.items())},
        "candidates_with_at_least_5_decoys": sum(value >= 5 for value in counts),
        "candidates_with_1_to_4_decoys": sum(1 <= value < 5 for value in counts),
        "candidate_directories_without_decoys": sum(value == 0 for value in counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.root, args.parent_pid), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
