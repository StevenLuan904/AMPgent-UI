from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

ADAPTER_VERSION = "amp-designer-v25-raw-topk10-batch100-v1"
EXPECTED_GENERATOR_ID = "amp_designer"
EXPECTED_RAW_BUDGET = 1000
EXPECTED_BATCH_SIZE = 100
EXPECTED_BATCHES = 10
EXPECTED_TOP_K = 10
EXPECTED_TOP_P = 1.0
EXPECTED_DECODE_STEPS = 34
EXPECTED_PROMPT_TOKENS = 10


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_request(request: dict[str, Any]) -> tuple[int, str]:
    if request.get("generator_id") != EXPECTED_GENERATOR_ID:
        raise ValueError("request generator_id must be amp_designer")
    if int(request.get("raw_proposal_budget", -1)) != EXPECTED_RAW_BUDGET:
        raise ValueError("v25 raw_proposal_budget must equal 1000")
    if int(request.get("batch_size", -1)) != EXPECTED_BATCH_SIZE:
        raise ValueError("v25 batch_size must equal 100")
    if int(request.get("batches", -1)) != EXPECTED_BATCHES:
        raise ValueError("v25 batches must equal 10")
    if int(request.get("top_k", -1)) != EXPECTED_TOP_K:
        raise ValueError("v25 top_k must equal 10")
    if float(request.get("top_p", -1.0)) != EXPECTED_TOP_P:
        raise ValueError("v25 top_p must equal 1.0")
    if int(request.get("decode_steps", -1)) != EXPECTED_DECODE_STEPS:
        raise ValueError("v25 decode_steps must equal 34")
    if request.get("temperature") is not None:
        raise ValueError("v25 does not add a temperature parameter")
    device = str(request.get("device", "")).lower()
    if device not in {"cpu", "cuda"}:
        raise ValueError("request device must be frozen as cpu or cuda")
    return int(request["seed"]), device


def _decode_token_strings(tokens: list[str]) -> tuple[str, int | None]:
    sequence: list[str] = []
    first_sep_position: int | None = None
    for position, token in enumerate(tokens, start=1):
        if token == "[SEP]":
            first_sep_position = position
            break
        sequence.append(token.upper())
    return "".join(sequence), first_sep_position


def _build_model(config_path: Path, weights_path: Path, device: str) -> Any:
    import torch
    from torch import nn
    from transformers.models.gpt2 import GPT2Config, GPT2LMHeadModel

    class SoftEmbedding(nn.Module):
        def __init__(self, embedding: nn.Embedding, n_tokens: int) -> None:
            super().__init__()
            self.wte = embedding
            self.n_tokens = n_tokens
            self.learned_embedding = nn.Parameter(
                embedding.weight[:n_tokens].clone().detach()
            )

        def forward(self, tokens: Any) -> Any:
            input_embedding = self.wte(tokens[:, self.n_tokens :])
            learned = self.learned_embedding.repeat(input_embedding.size(0), 1, 1)
            return torch.cat([learned, input_embedding], dim=1)

    config = GPT2Config.from_json_file(str(config_path))
    model = GPT2LMHeadModel(config)
    model.set_input_embeddings(
        SoftEmbedding(model.get_input_embeddings(), EXPECTED_PROMPT_TOKENS)
    )
    try:
        state = torch.load(weights_path, map_location="cpu", weights_only=True)
    except TypeError as exc:
        raise RuntimeError("PyTorch runtime must support weights_only=True") from exc
    if not isinstance(state, dict):
        raise ValueError("AMP-Designer weight payload must be a state dictionary")
    model.load_state_dict(state, strict=True)
    model.to(torch.device(device))
    model.eval()
    return model


def _filter_logits(logits: Any, *, top_k: int, top_p: float) -> Any:
    import torch

    if top_k <= 0:
        raise ValueError("top_k must be positive")
    top_k = min(top_k, logits.size(-1))
    remove = logits < torch.topk(logits, top_k)[0][..., -1, None]
    logits = logits.masked_fill(remove, -float("inf"))
    if 0.0 < top_p < 1.0:
        sorted_logits, sorted_indices = torch.sort(logits, descending=True)
        cumulative = sorted_logits.softmax(dim=-1).cumsum(dim=-1)
        sorted_remove = cumulative > top_p
        sorted_remove[..., 1:] = sorted_remove[..., :-1].clone()
        sorted_remove[..., 0] = False
        remove = sorted_remove.scatter(-1, sorted_indices, sorted_remove)
        logits = logits.masked_fill(remove, -float("inf"))
    return logits


def _generate_batch(model: Any, tokenizer: Any, *, device: str) -> list[list[str]]:
    import torch
    import torch.nn.functional as functional

    input_tensor = torch.zeros(EXPECTED_BATCH_SIZE, 11, dtype=torch.long)
    for index, token_id in enumerate(range(100, 100 + EXPECTED_PROMPT_TOKENS)):
        input_tensor[:, index] = token_id
    input_tensor = input_tensor.to(torch.device(device))
    finished = torch.zeros(EXPECTED_BATCH_SIZE, 1, dtype=torch.bool, device=device)
    generated: list[Any] = []
    with torch.inference_mode():
        for _ in range(EXPECTED_DECODE_STEPS):
            outputs = model(input_ids=input_tensor)
            if not hasattr(outputs, "logits"):
                raise ValueError("AMP-Designer forward output is missing logits")
            logits = _filter_logits(
                outputs.logits,
                top_k=EXPECTED_TOP_K,
                top_p=EXPECTED_TOP_P,
            )
            probabilities = functional.softmax(logits[:, -1, :], dim=-1)
            last_token_id = torch.multinomial(probabilities, 1)
            finished |= last_token_id == tokenizer.sep_token_id
            generated.append(last_token_id.detach().cpu())
            if bool(torch.all(finished)):
                break
            input_tensor = torch.cat((input_tensor, last_token_id), dim=1)
    if not generated:
        raise ValueError("AMP-Designer generated no token steps")
    matrix = torch.cat(generated, dim=1).tolist()
    return [tokenizer.convert_ids_to_tokens(row) for row in matrix]


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
    model = _build_model(config_path, weights_path, device)
    records: list[dict[str, Any]] = []
    raw_rank = 0
    for batch_index in range(1, EXPECTED_BATCHES + 1):
        batch = _generate_batch(model, tokenizer, device=device)
        if len(batch) != EXPECTED_BATCH_SIZE:
            raise ValueError("AMP-Designer returned an incomplete fixed batch")
        for row_index, tokens in enumerate(batch, start=1):
            raw_rank += 1
            sequence, first_sep_position = _decode_token_strings(tokens)
            records.append(
                {
                    "raw_rank": raw_rank,
                    "batch_index": batch_index,
                    "row_index": row_index,
                    "raw_tokens": tokens,
                    "first_sep_position": first_sep_position,
                    "sequence": sequence,
                }
            )
    if len(records) != EXPECTED_RAW_BUDGET:
        raise ValueError("AMP-Designer raw count drifted from the frozen budget")
    return {
        "generator_id": EXPECTED_GENERATOR_ID,
        "seed": seed,
        "device": device,
        "raw_proposal_budget": EXPECTED_RAW_BUDGET,
        "records": records,
        "artifacts": [
            {
                "path": config_path.name,
                "size_bytes": config_path.stat().st_size,
                "sha256": _sha256(config_path),
            },
            {
                "path": weights_path.name,
                "size_bytes": weights_path.stat().st_size,
                "sha256": _sha256(weights_path),
            },
            {
                "path": vocab_path.as_posix(),
                "size_bytes": vocab_path.stat().st_size,
                "sha256": _sha256(vocab_path),
            },
        ],
        "sampling": {
            "top_k": EXPECTED_TOP_K,
            "top_p": EXPECTED_TOP_P,
            "temperature": None,
            "decode_steps": EXPECTED_DECODE_STEPS,
            "learned_prompt_tokens": EXPECTED_PROMPT_TOKENS,
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
    print(json.dumps({"generator_id": EXPECTED_GENERATOR_ID, "records": 1000}))


if __name__ == "__main__":
    main()
