from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
_SOURCE_KINDS = frozenset(
    {"authority_protein", "authority_mrna_resolved", "public_canonical_supplement"}
)


@dataclass(frozen=True)
class TargetSequence:
    target_key: str
    display_name: str
    organism: str
    requested_candidate_count: int
    authority_accession: str | None
    protein_accession: str
    source_kind: str
    source_uri: str
    sequence: str
    sequence_sha256: str
    is_partial: bool


def sequence_sha256(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def _require_text(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"target field {key!r} must be non-empty text")
    return value.strip()


def load_target_sequence_manifest(path: str | Path) -> tuple[TargetSequence, ...]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "ampgent.target_sequence_manifest.v1":
        raise ValueError("unsupported target sequence manifest schema_version")

    records = manifest.get("targets")
    if not isinstance(records, list) or not records:
        raise ValueError("manifest targets must be a non-empty list")

    targets: list[TargetSequence] = []
    seen_keys: set[str] = set()
    seen_accessions: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("each target entry must be an object")
        target_key = _require_text(record, "target_key")
        if target_key in seen_keys:
            raise ValueError(f"duplicate target_key: {target_key}")
        seen_keys.add(target_key)

        protein_accession = _require_text(record, "protein_accession")
        if protein_accession in seen_accessions:
            raise ValueError(f"duplicate protein_accession: {protein_accession}")
        seen_accessions.add(protein_accession)

        sequence = _require_text(record, "sequence").upper()
        invalid = sorted(set(sequence) - _AMINO_ACIDS)
        if invalid:
            raise ValueError(f"{target_key} has invalid amino acids: {invalid}")
        if record.get("sequence_length") != len(sequence):
            raise ValueError(f"{target_key} sequence_length does not match sequence")
        expected_sha = _require_text(record, "sequence_sha256").lower()
        if sequence_sha256(sequence) != expected_sha:
            raise ValueError(f"{target_key} sequence_sha256 does not match sequence")

        source_kind = _require_text(record, "source_kind")
        if source_kind not in _SOURCE_KINDS:
            raise ValueError(f"{target_key} has unsupported source_kind: {source_kind}")
        requested_count = record.get("requested_candidate_count")
        if not isinstance(requested_count, int) or requested_count <= 0:
            raise ValueError(f"{target_key} requested_candidate_count must be positive")

        authority_accession = record.get("authority_accession")
        if authority_accession is not None and not isinstance(authority_accession, str):
            raise ValueError(f"{target_key} authority_accession must be text or null")

        targets.append(
            TargetSequence(
                target_key=target_key,
                display_name=_require_text(record, "display_name"),
                organism=_require_text(record, "organism"),
                requested_candidate_count=requested_count,
                authority_accession=authority_accession,
                protein_accession=protein_accession,
                source_kind=source_kind,
                source_uri=_require_text(record, "source_uri"),
                sequence=sequence,
                sequence_sha256=expected_sha,
                is_partial=bool(record.get("is_partial", False)),
            )
        )

    expected_count = manifest.get("target_count")
    if expected_count != len(targets):
        raise ValueError("target_count does not match targets")
    expected_total = manifest.get("requested_target_specific_total")
    if expected_total != sum(target.requested_candidate_count for target in targets):
        raise ValueError("requested_target_specific_total does not match targets")
    return tuple(targets)
