from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def iter_bundle_items(root: Path):
    receipt_paths = list(root.glob("candidates/*/*/completion_receipt.json"))
    receipt_paths.extend(
        root.glob("candidates/*/*/protocols/coarse5/completion_receipt.json")
    )
    for receipt_path in sorted(receipt_paths, key=lambda path: str(path)):
        receipt_text = receipt_path.read_text(encoding="utf-8")
        receipt = json.loads(receipt_text)
        if (
            receipt.get("schema_version")
            != "ampgent.autoresearch-rosetta-candidate-completion.1"
            or receipt.get("status") != "succeeded"
        ):
            continue
        result_path = receipt_path.parent / receipt.get(
            "result_relative_path", "results/rosetta_result.json"
        )
        if not result_path.is_file():
            continue
        yield {
            "schema_version": "ampgent.rosetta-receipt-bundle-item.1",
            "receipt_path": str(receipt_path.resolve()),
            "receipt_json": receipt_text,
            "result_json": result_path.read_text(encoding="utf-8"),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    for item in iter_bundle_items(args.root):
        sys.stdout.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
