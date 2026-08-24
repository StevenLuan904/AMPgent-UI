from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "ChatterjeeLab/PepMLM-650M"
DEFAULT_REVISION = "898fca941a9057aebdd1a6164b5ee09a1a71780e"
CANONICAL_AA = frozenset("ACDEFGHIKLMNPQRSTVWY")


def _normalize_sequence(value: str, *, field: str) -> str:
    sequence = "".join(value.split()).upper()
    if not sequence or set(sequence) - CANONICAL_AA:
        raise ValueError(f"{field} must contain only canonical amino acids")
    return sequence


def _require_sha256(sequence: str, expected: str, *, field: str) -> None:
    actual = hashlib.sha256(sequence.encode("utf-8")).hexdigest()
    if actual != expected:
        raise ValueError(f"{field} sequence_sha256 mismatch: expected {expected}, got {actual}")


def validate_request(request: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    target = dict(request["target"])
    for key in ("target_key", "accession", "sequence", "sequence_sha256"):
        if not isinstance(target.get(key), str) or not target[key]:
            raise ValueError(f"target.{key} is required")
    target["sequence"] = _normalize_sequence(target["sequence"], field="target.sequence")
    _require_sha256(
        target["sequence"], target["sequence_sha256"], field="target"
    )
    raw_peptides = request.get("peptides")
    if not isinstance(raw_peptides, list) or not raw_peptides:
        raise ValueError("peptides must be a non-empty list")
    peptides: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_peptides):
        peptide = dict(raw)
        if not isinstance(peptide.get("candidate_id"), str) or not peptide[
            "candidate_id"
        ]:
            raise ValueError(f"peptides[{index}].candidate_id is required")
        peptide["sequence"] = _normalize_sequence(
            str(peptide["sequence"]), field=f"peptides[{index}].sequence"
        )
        _require_sha256(
            peptide["sequence"],
            str(peptide["sequence_sha256"]),
            field=f"peptides[{index}]",
        )
        if peptide["sequence_sha256"] in seen:
            raise ValueError("peptide sequence identities must be unique")
        seen.add(peptide["sequence_sha256"])
        peptides.append(peptide)
    return target, peptides


def main() -> None:
    import torch
    from transformers import AutoModelForMaskedLM, AutoTokenizer

    from pepagent.model_workers.pepmlm_target_proxy_cli import pseudo_perplexity

    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    target, peptides = validate_request(request)
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
    results: list[dict[str, Any]] = []
    for peptide in peptides:
        nll, ppl, residue_logs = pseudo_perplexity(
            model, tokenizer, target["sequence"], peptide["sequence"]
        )
        results.append(
            {
                **peptide,
                "conditional_nll": nll,
                "conditional_ppl": ppl,
                "per_residue_log_probabilities": residue_logs,
            }
        )
    output = {
        "schema_version": "ampgent.pepmlm-target-conditional.1",
        "metric_version": "pepmlm-650m-conditional-ppl.1",
        "definition": "mean masked-residue NLL of peptide conditioned on target sequence",
        "model": model_name,
        "revision": revision,
        "device": device,
        "target": target,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
