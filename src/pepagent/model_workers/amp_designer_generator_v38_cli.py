from __future__ import annotations

import argparse
import importlib.util
import json
import random
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_legacy_adapter() -> ModuleType:
    adapter_path = Path(__file__).with_name("amp_designer_generator_cli.py")
    spec = importlib.util.spec_from_file_location(
        "pepagent_amp_designer_generator_legacy",
        adapter_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load AMP-Designer legacy adapter: {adapter_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = _load_legacy_adapter()

ADAPTER_VERSION = "amp-designer-v38-score-all-batch100-v1"
REQUEST_SCHEMA = "v38.generator-request.1"
EXPECTED_RAW_BUDGET = 100
EXPECTED_BATCH_SIZE = 100
EXPECTED_BATCHES = 1


def _validate_request(request: dict[str, Any]) -> tuple[int, str]:
    if request.get("schema_version") != REQUEST_SCHEMA:
        raise ValueError("v38 AMP-Designer request schema is invalid")
    if request.get("generator_id") != legacy.EXPECTED_GENERATOR_ID:
        raise ValueError("request generator_id must be amp_designer")
    expected = {
        "raw_proposal_budget": EXPECTED_RAW_BUDGET,
        "batch_size": EXPECTED_BATCH_SIZE,
        "batches": EXPECTED_BATCHES,
        "top_k": legacy.EXPECTED_TOP_K,
        "decode_steps": legacy.EXPECTED_DECODE_STEPS,
    }
    for field, value in expected.items():
        if int(request.get(field, -1)) != value:
            raise ValueError(f"v38 AMP-Designer {field} must equal {value}")
    if float(request.get("top_p", -1.0)) != legacy.EXPECTED_TOP_P:
        raise ValueError("v38 AMP-Designer top_p must equal 1.0")
    if request.get("temperature") is not None:
        raise ValueError("v38 AMP-Designer does not add a temperature parameter")
    device = str(request.get("device", "")).lower()
    if device not in {"cpu", "cuda"}:
        raise ValueError("request device must be frozen as cpu or cuda")
    return int(request["seed"]), device


def generate(
    request: dict[str, Any],
    *,
    config_path: Path,
    weights_path: Path,
    vocab_path: Path,
) -> dict[str, Any]:
    import numpy as np
    import torch
    from transformers import BertTokenizer

    seed, device = _validate_request(request)
    for path in (config_path, weights_path, vocab_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    if any(marker in weights_path.name.lower() for marker in ("regress", "reward")):
        raise ValueError("internal scoring weights are forbidden")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("frozen CUDA device requested but unavailable")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if device == "cuda":
        torch.cuda.manual_seed_all(seed)
    tokenizer = BertTokenizer(vocab_file=str(vocab_path))
    model = legacy._build_model(config_path, weights_path, device)
    tokens_by_row = legacy._generate_batch(model, tokenizer, device=device)
    if len(tokens_by_row) != EXPECTED_BATCH_SIZE:
        raise ValueError("AMP-Designer returned an incomplete v38 batch")
    records = []
    for raw_rank, tokens in enumerate(tokens_by_row, start=1):
        sequence, first_sep_position = legacy._decode_token_strings(tokens)
        records.append(
            {
                "raw_rank": raw_rank,
                "batch_index": 1,
                "row_index": raw_rank,
                "raw_tokens": tokens,
                "first_sep_position": first_sep_position,
                "sequence": sequence,
            }
        )
    return {
        "generator_id": legacy.EXPECTED_GENERATOR_ID,
        "seed": seed,
        "device": device,
        "raw_proposal_budget": EXPECTED_RAW_BUDGET,
        "records": records,
        "artifacts": [
            {
                "path": path.name if path != vocab_path else path.as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": legacy._sha256(path),
            }
            for path in (config_path, weights_path, vocab_path)
        ],
        "sampling": {
            "top_k": legacy.EXPECTED_TOP_K,
            "top_p": legacy.EXPECTED_TOP_P,
            "temperature": None,
            "decode_steps": legacy.EXPECTED_DECODE_STEPS,
            "learned_prompt_tokens": legacy.EXPECTED_PROMPT_TOKENS,
            "batch_size": EXPECTED_BATCH_SIZE,
            "batches": EXPECTED_BATCHES,
        },
        "adapter_version": ADAPTER_VERSION,
        "internal_score_filtering_enabled": False,
        "internal_regressors_loaded": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--vocab", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    result = generate(
        request,
        config_path=args.config,
        weights_path=args.weights,
        vocab_path=args.vocab,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "generator_id": legacy.EXPECTED_GENERATOR_ID,
                "records": len(result["records"]),
            }
        )
    )


if __name__ == "__main__":
    main()
