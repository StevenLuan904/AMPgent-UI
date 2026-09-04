"""Build source-effect benchmark v3 by adding one explicit run-scoped run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.build_source_effect_benchmark import build_benchmark, write_csv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--addition-config", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.base_config.read_text(encoding="utf-8"))
    additions = json.loads(args.addition_config.read_text(encoding="utf-8"))
    config["benchmark_id"] = additions["benchmark_id"]
    config["observed_at_utc"] = additions["observed_at_utc"]
    config["specs"] = [*config["specs"], *additions["specs"]]
    result = build_benchmark(config, args.base_config.resolve().parents[2])
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(result["records"], args.output_csv)
    print(
        json.dumps(
            {
                "benchmark_id": result["benchmark_id"],
                "record_count": len(result["records"]),
                "output_json": str(args.output_json),
                "output_csv": str(args.output_csv),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
