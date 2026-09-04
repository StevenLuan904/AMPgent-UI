"""Build benchmark v7 from the frozen v2 base plus v6 and v7 additions."""

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
    parser.add_argument("--addition-config", type=Path, action="append", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.base_config.read_text(encoding="utf-8"))
    additions = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.addition_config
    ]
    config["benchmark_id"] = additions[-1]["benchmark_id"]
    config["observed_at_utc"] = additions[-1]["observed_at_utc"]
    if "selection_exclusions" in additions[-1]:
        config["selection_exclusions"] = additions[-1]["selection_exclusions"]
    if "next_operator_override" in additions[-1]:
        config["next_operator_override"] = additions[-1]["next_operator_override"]
    config["specs"] = [
        *config["specs"],
        *(spec for addition in additions for spec in addition["specs"]),
    ]
    result = build_benchmark(config, args.base_config.resolve().parents[2])
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(result["records"], args.output_csv)
    print(
        json.dumps(
            {
                "record_count": len(result["records"]),
                "benchmark_id": result["benchmark_id"],
            }
        )
    )


if __name__ == "__main__":
    main()
