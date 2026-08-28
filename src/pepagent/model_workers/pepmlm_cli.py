import argparse
import hashlib
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


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_action(action: dict[str, Any]) -> dict[str, Any]:
    """Validate one frozen, machine-executable AutoResearch action."""
    normalized = dict(action)
    kind = str(normalized.get("action_kind") or normalized.get("kind") or "")
    if kind not in {
        "masked_substitution",
        "controlled_crossover",
        "de_novo",
        "unchanged_control",
    }:
        raise ValueError(f"unsupported autoresearch action kind: {kind!r}")
    normalized["action_kind"] = kind
    declared_sha = normalized.pop("action_sha256", None)
    computed_sha = canonical_sha256(normalized)
    if declared_sha is not None and str(declared_sha) != computed_sha:
        raise ValueError("autoresearch action SHA-256 does not match its canonical payload")
    normalized["action_sha256"] = computed_sha
    normalized.setdefault("action_id", computed_sha[:24])
    return normalized


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def sample_canonical_tokens(logits: Any, tokenizer: Any, top_k: int) -> Any:
    """Sample only from the 20 canonical residue tokens, even when X ranks highly."""
    if top_k < 1:
        raise ValueError("top_k must be positive")
    residues = sorted(CANONICAL_AA)
    token_ids = [int(item) for item in tokenizer.convert_tokens_to_ids(residues)]
    if len(set(token_ids)) != len(residues):
        raise ValueError("tokenizer does not map canonical residues to distinct tokens")
    unknown_id = getattr(tokenizer, "unk_token_id", None)
    if unknown_id is not None and int(unknown_id) in token_ids:
        raise ValueError("tokenizer maps a canonical residue to the unknown token")
    canonical_ids = torch.tensor(token_ids, device=logits.device, dtype=torch.long)
    canonical_logits = logits.index_select(-1, canonical_ids)
    top_logits, top_offsets = canonical_logits.topk(min(top_k, len(token_ids)), dim=-1)
    sampled = Categorical(logits=top_logits).sample()
    selected_offsets = top_offsets.gather(-1, sampled.unsqueeze(-1)).squeeze(-1)
    return canonical_ids[selected_offsets]


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
    token_ids = sample_canonical_tokens(mask_logits, tokenizer, top_k)
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
        inference_logits = model(masked).logits[0, selected_positions] / temperature
    # A clone created inside inference mode is still an inference tensor. Clone only after leaving
    # the context so the deliberate in-place exclusion works on PyTorch 2.6+.
    logits = inference_logits.clone()
    logits[torch.arange(mutation_count, device=model.device), original_tokens] = -torch.inf
    replacement_tokens = sample_canonical_tokens(logits, tokenizer, top_k)
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


def mutate_one_at_positions(
    model: Any,
    tokenizer: Any,
    target: str,
    parent: str,
    mutation_positions: list[int],
    top_k: int,
    temperature: float,
    seed: int,
) -> dict[str, Any]:
    """Mutate exactly the 1-based positions frozen by the Research Director."""
    if not parent or not set(parent).issubset(CANONICAL_AA):
        raise ValueError("action parent must be a non-empty canonical peptide")
    positions = [int(item) for item in mutation_positions]
    if not positions or len(positions) != len(set(positions)):
        raise ValueError("masked substitution requires unique explicit positions")
    if min(positions) < 1 or max(positions) > len(parent):
        raise ValueError("masked substitution position lies outside the parent")
    seed_everything(seed)
    offsets = [item - 1 for item in sorted(positions)]
    encoded = tokenizer.encode(target + parent, return_tensors="pt").to(model.device)
    peptide_positions = torch.arange(-len(parent) - 1, -1, device=model.device)
    selected_positions = peptide_positions[offsets]
    original_tokens = encoded[0, selected_positions].clone()
    masked = encoded.clone()
    masked[0, selected_positions] = tokenizer.mask_token_id
    with torch.inference_mode():
        inference_logits = model(masked).logits[0, selected_positions] / temperature
    logits = inference_logits.clone()
    logits[torch.arange(len(offsets), device=model.device), original_tokens] = -torch.inf
    replacements = sample_canonical_tokens(logits, tokenizer, top_k)
    mutated = encoded.clone()
    mutated[0, selected_positions] = replacements
    peptide = tokenizer.decode(
        mutated[0, peptide_positions], skip_special_tokens=True
    ).replace(" ", "")
    if peptide == parent:
        raise ValueError("action proposal did not change its parent")
    if len(peptide) != len(parent) or not set(peptide).issubset(CANONICAL_AA):
        raise ValueError(f"non-canonical or malformed action peptide: {peptide!r}")
    nll, ppl, residue_logs = pseudo_perplexity(model, tokenizer, target, peptide)
    return {
        "sequence": peptide,
        "conditional_nll": nll,
        "conditional_ppl": ppl,
        "per_residue_log_probabilities": residue_logs,
        "seed": seed,
        "proposal_mode": "agent_masked_substitution",
        "parent_sequence": parent,
        "mutation_positions": [item + 1 for item in offsets],
    }


def controlled_crossover_base(
    primary: str,
    donor: str,
    primary_start: int,
    primary_end: int,
    donor_start: int | None = None,
    donor_end: int | None = None,
) -> tuple[str, dict[str, int]]:
    """Create a deterministic donor-segment replacement with auditable coordinates."""
    if not primary or not donor:
        raise ValueError("controlled crossover requires primary and donor sequences")
    if not set(primary).issubset(CANONICAL_AA) or not set(donor).issubset(CANONICAL_AA):
        raise ValueError("controlled crossover parents must be canonical peptides")
    donor_start = primary_start if donor_start is None else int(donor_start)
    donor_end = primary_end if donor_end is None else int(donor_end)
    primary_start, primary_end = int(primary_start), int(primary_end)
    if not (1 <= primary_start <= primary_end <= len(primary)):
        raise ValueError("primary crossover coordinates are invalid")
    if not (1 <= donor_start <= donor_end <= len(donor)):
        raise ValueError("donor crossover coordinates are invalid")
    child = (
        primary[: primary_start - 1]
        + donor[donor_start - 1 : donor_end]
        + primary[primary_end:]
    )
    if child == primary:
        raise ValueError("controlled crossover did not change the primary parent")
    if not 5 <= len(child) <= 60 or not set(child).issubset(CANONICAL_AA):
        raise ValueError("controlled crossover produced an invalid peptide")
    return child, {
        "primary_start": primary_start,
        "primary_end": primary_end,
        "donor_start": donor_start,
        "donor_end": donor_end,
    }


def score_existing(
    model: Any,
    tokenizer: Any,
    target: str,
    peptide: str,
    *,
    seed: int,
    proposal_mode: str,
    parent_sequence: str | None,
) -> dict[str, Any]:
    if not peptide or not set(peptide).issubset(CANONICAL_AA):
        raise ValueError("existing peptide must be canonical")
    seed_everything(seed)
    nll, ppl, residue_logs = pseudo_perplexity(model, tokenizer, target, peptide)
    return {
        "sequence": peptide,
        "conditional_nll": nll,
        "conditional_ppl": ppl,
        "per_residue_log_probabilities": residue_logs,
        "seed": seed,
        "proposal_mode": proposal_mode,
        "parent_sequence": parent_sequence,
        "mutation_positions": [],
    }


def execute_action(
    model: Any,
    tokenizer: Any,
    target: str,
    action: dict[str, Any],
    *,
    default_top_k: int,
    default_temperature: float,
    sampling_seed: int | None = None,
    sampling_attempt: int = 0,
) -> dict[str, Any]:
    action = validate_action(action)
    kind = action["action_kind"]
    action_seed = int(action["seed"])
    seed = action_seed if sampling_seed is None else int(sampling_seed)
    top_k = int(action.get("top_k", default_top_k))
    temperature = float(action.get("temperature", default_temperature))
    primary = str(
        action.get("primary_parent_sequence") or action.get("parent_sequence") or ""
    ).upper()
    if kind == "de_novo":
        result = generate_one(
            model,
            tokenizer,
            target,
            int(action["peptide_length"]),
            top_k,
            temperature,
            seed,
        )
    elif kind == "masked_substitution":
        result = mutate_one_at_positions(
            model,
            tokenizer,
            target,
            primary,
            list(action["mutation_positions"]),
            top_k,
            temperature,
            seed,
        )
    elif kind == "controlled_crossover":
        donor = str(action["donor_sequence"]).upper()
        crossover = dict(action["crossover"])
        base, coordinates = controlled_crossover_base(
            primary,
            donor,
            crossover["primary_start"],
            crossover["primary_end"],
            crossover.get("donor_start"),
            crossover.get("donor_end"),
        )
        positions = list(action.get("mutation_positions", []))
        if positions:
            result = mutate_one_at_positions(
                model,
                tokenizer,
                target,
                base,
                positions,
                top_k,
                temperature,
                seed,
            )
            result["proposal_mode"] = "agent_controlled_crossover_refinement"
            result["parent_sequence"] = primary
        else:
            result = score_existing(
                model,
                tokenizer,
                target,
                base,
                seed=seed,
                proposal_mode="agent_controlled_crossover",
                parent_sequence=primary,
            )
        result["donor_sequence"] = donor
        result["crossover"] = coordinates
    else:
        result = score_existing(
            model,
            tokenizer,
            target,
            primary,
            seed=seed,
            proposal_mode="unchanged_parent_control",
            parent_sequence=primary,
        )
    result.update(
        {
            "action_id": action["action_id"],
            "action_sha256": action["action_sha256"],
            "action_kind": kind,
            "action_seed": action_seed,
            "sampling_seed": seed,
            "sampling_attempt": sampling_attempt,
            "primary_parent_id": action.get("primary_parent_id"),
            "donor_candidate_id": action.get("donor_candidate_id"),
            "expected_improvement_axes": action.get("expected_improvement_axes", []),
            "protected_axes": action.get("protected_axes", []),
        }
    )
    return result


def execute_unique_action_with_retry(
    model: Any,
    tokenizer: Any,
    target: str,
    action: dict[str, Any],
    *,
    default_top_k: int,
    default_temperature: float,
    seen: set[str],
    max_attempts: int,
) -> dict[str, Any]:
    """Execute one frozen action with a deterministic, replayable seed schedule."""
    if max_attempts < 1:
        raise ValueError("action max_attempts must be positive")
    frozen = validate_action(action)
    base_seed = int(frozen["seed"])
    failures: list[str] = []
    for attempt in range(max_attempts):
        sampling_seed = base_seed + attempt
        try:
            result = execute_action(
                model,
                tokenizer,
                target,
                frozen,
                default_top_k=default_top_k,
                default_temperature=default_temperature,
                sampling_seed=sampling_seed,
                sampling_attempt=attempt,
            )
        except ValueError as error:
            failures.append(f"seed={sampling_seed}: {error}")
            continue
        if result["proposal_mode"] == "unchanged_parent_control":
            return result
        if result["sequence"] in seen:
            failures.append(f"seed={sampling_seed}: duplicate sequence")
            continue
        seen.add(result["sequence"])
        return result
    detail = "; ".join(failures[-3:])
    raise ValueError(
        f"action {frozen['action_id']} exhausted {max_attempts} deterministic attempts: {detail}"
    )


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
    actions = [validate_action(item) for item in request.get("action_plans", [])]
    parents = [str(parent).upper() for parent in request.get("parent_sequences", [])]
    if actions:
        requested_count = len(actions)
        for action in actions:
            max_attempts = int(request.get("action_max_attempts", 20))
            result = execute_unique_action_with_retry(
                model,
                tokenizer,
                target,
                action,
                default_top_k=int(request.get("top_k", 5)),
                default_temperature=float(request.get("temperature", 1.0)),
                seen=seen,
                max_attempts=max_attempts,
            )
            attempts += int(result["sampling_attempt"]) + 1
            results.append(result)
    elif parents:
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

    mutation_briefs = request.get("mutation_briefs", [])
    output = {
        "schema_version": "1.0",
        "model": model_name,
        "revision": revision,
        "device": device,
        "requested_count": requested_count,
        "generated_count": len(results),
        "attempts": attempts,
        "proposal_mode": (
            "autoresearch_action_batch"
            if actions
            else "parent_masked_mutation"
            if parents
            else "de_novo"
        ),
        "mutation_guidance_receipt": {
            "brief_sha256s": [item["brief_sha256"] for item in mutation_briefs],
            "natural_language_consumed": False,
            "machine_executable_action_count": len(actions),
            "action_sha256s": [item["action_sha256"] for item in actions],
            "reason": (
                "PepMLM is a masked language model; advisory evidence is recorded for "
                "the Director but is not silently treated as a sampling control."
            ),
        },
        "candidates": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
