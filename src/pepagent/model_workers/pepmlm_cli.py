import argparse
import json
import math
import random
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.distributions import Categorical
from transformers import AutoModelForMaskedLM, AutoTokenizer

DEFAULT_MODEL = "ChatterjeeLab/PepMLM-650M"
DEFAULT_REVISION = "898fca941a9057aebdd1a6164b5ee09a1a71780e"
CANONICAL_AA = set("ACDEFGHIKLMNPQRSTVWY")


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def pseudo_perplexity(
    model: Any, tokenizer: Any, target: str, peptide: str
) -> tuple[float, float, list[float]]:
    encoded = tokenizer.encode(target + peptide, return_tensors="pt").to(model.device)
    peptide_length = len(peptide)
    masked = encoded.repeat(peptide_length, 1)
    positions = torch.arange(-peptide_length - 1, -1, device=model.device)
    row_ids = torch.arange(peptide_length, device=model.device)
    labels = encoded[0, positions]
    masked[row_ids, positions] = tokenizer.mask_token_id
    with torch.inference_mode():
        logits = model(masked).logits[row_ids, positions]
        log_probs = torch.log_softmax(logits, dim=-1)
        residue_log_probs = log_probs[row_ids, labels]
    nll = -float(residue_log_probs.mean().item())
    return nll, math.exp(nll), [float(v) for v in residue_log_probs.cpu().tolist()]


def generate_one(
    model: Any,
    tokenizer: Any,
    target: str,
    length: int,
    top_k: int,
    temperature: float,
    seed: int,
) -> dict[str, Any]:
    seed_everything(seed)
    model_input = tokenizer(target + tokenizer.mask_token * length, return_tensors="pt").to(
        model.device
    )
    with torch.inference_mode():
        logits = model(**model_input).logits
    mask_positions = (model_input["input_ids"] == tokenizer.mask_token_id).nonzero(as_tuple=True)[1]
    mask_logits = logits[0, mask_positions] / temperature
    top_logits, top_indices = mask_logits.topk(top_k, dim=-1)
    sampled = Categorical(logits=top_logits).sample()
    token_ids = top_indices.gather(-1, sampled.unsqueeze(-1)).squeeze(-1)
    peptide = tokenizer.decode(token_ids, skip_special_tokens=True).replace(" ", "")
    if len(peptide) != length or not set(peptide).issubset(CANONICAL_AA):
        raise ValueError(f"non-canonical or malformed generated peptide: {peptide!r}")
    nll, ppl, residue_logs = pseudo_perplexity(model, tokenizer, target, peptide)
    return {
        "sequence": peptide,
        "conditional_nll": nll,
        "conditional_ppl": ppl,
        "per_residue_log_probabilities": residue_logs,
        "seed": seed,
        "proposal_mode": "de_novo",
        "parent_sequence": None,
        "mutation_positions": [],
    }


def mutate_one(
    model: Any,
    tokenizer: Any,
    target: str,
    parent: str,
    mutation_count: int,
    top_k: int,
    temperature: float,
    seed: int,
) -> dict[str, Any]:
    seed_everything(seed)
    generator = random.Random(seed)
    mutation_count = min(mutation_count, len(parent))
    mutation_offsets = sorted(generator.sample(range(len(parent)), mutation_count))
    encoded = tokenizer.encode(target + parent, return_tensors="pt").to(model.device)
    peptide_positions = torch.arange(-len(parent) - 1, -1, device=model.device)
    selected_positions = peptide_positions[mutation_offsets]
    original_tokens = encoded[0, selected_positions].clone()
    masked = encoded.clone()
    masked[0, selected_positions] = tokenizer.mask_token_id
    with torch.inference_mode():
        logits = model(masked).logits[0, selected_positions] / temperature
    logits[torch.arange(mutation_count, device=model.device), original_tokens] = -torch.inf
    top_logits, top_indices = logits.topk(top_k, dim=-1)
    sampled = Categorical(logits=top_logits).sample()
    replacement_tokens = top_indices.gather(-1, sampled.unsqueeze(-1)).squeeze(-1)
    mutated = encoded.clone()
    mutated[0, selected_positions] = replacement_tokens
    peptide = tokenizer.decode(mutated[0, peptide_positions], skip_special_tokens=True).replace(
        " ", ""
    )
    if peptide == parent:
        raise ValueError("mutation proposal did not change its parent")
    if len(peptide) != len(parent) or not set(peptide).issubset(CANONICAL_AA):
        raise ValueError(f"non-canonical or malformed mutated peptide: {peptide!r}")
    nll, ppl, residue_logs = pseudo_perplexity(model, tokenizer, target, peptide)
    return {
        "sequence": peptide,
        "conditional_nll": nll,
        "conditional_ppl": ppl,
        "per_residue_log_probabilities": residue_logs,
        "seed": seed,
        "proposal_mode": "parent_masked_mutation",
        "parent_sequence": parent,
        "mutation_positions": [offset + 1 for offset in mutation_offsets],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    model_name = request.get("model", DEFAULT_MODEL)
    revision = request.get("revision", DEFAULT_REVISION)
    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=revision)
    model = AutoModelForMaskedLM.from_pretrained(
        model_name,
        revision=revision,
        dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).eval()

    target = "".join(request["target_sequence"].split()).upper()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    base_seed = int(request["seed"])
    attempts = 0
    parents = [str(parent).upper() for parent in request.get("parent_sequences", [])]
    if parents:
        children_per_parent = int(request["children_per_parent"])
        requested_count = children_per_parent * len(parents)
        minimum = int(request.get("mutation_count_min", 1))
        maximum = int(request.get("mutation_count_max", 3))
        for parent_index, parent in enumerate(parents):
            produced = 0
            local_attempts = 0
            while produced < children_per_parent and local_attempts < children_per_parent * 30:
                candidate_seed = base_seed + parent_index * 100_000 + local_attempts
                mutation_count = minimum + local_attempts % (maximum - minimum + 1)
                local_attempts += 1
                attempts += 1
                try:
                    result = mutate_one(
                        model,
                        tokenizer,
                        target,
                        parent,
                        mutation_count,
                        int(request.get("top_k", 5)),
                        float(request.get("temperature", 1.0)),
                        candidate_seed,
                    )
                except ValueError:
                    continue
                if result["sequence"] in seen:
                    continue
                seen.add(result["sequence"])
                results.append(result)
                produced += 1
    else:
        requested_count = int(request["count"])
        while len(results) < requested_count and attempts < requested_count * 20:
            candidate_seed = base_seed + attempts
            attempts += 1
            try:
                result = generate_one(
                    model,
                    tokenizer,
                    target,
                    int(request["peptide_length"]),
                    int(request.get("top_k", 3)),
                    float(request.get("temperature", 1.0)),
                    candidate_seed,
                )
            except ValueError:
                continue
            if result["sequence"] in seen:
                continue
            seen.add(result["sequence"])
            results.append(result)

    output = {
        "schema_version": "1.0",
        "model": model_name,
        "revision": revision,
        "device": device,
        "requested_count": requested_count,
        "generated_count": len(results),
        "attempts": attempts,
        "proposal_mode": "parent_masked_mutation" if parents else "de_novo",
        "candidates": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
