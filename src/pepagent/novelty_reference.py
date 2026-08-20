from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

from pepagent.provenance.hashing import sha256_json, sha256_text

CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


@dataclass(frozen=True)
class FrozenFastaReference:
    schema_version: str
    source_record_count: int
    accepted_record_count: int
    unique_sequence_count: int
    duplicate_record_count: int
    rejected_length_count: int
    rejected_noncanonical_count: int
    minimum_length: int
    maximum_length: int
    normalized_fasta_sha256: str
    sequence_set_sha256: str

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)


def _parse_fasta(text: str) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    identifier: str | None = None
    sequence_parts: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if identifier is not None:
                records.append((identifier, "".join(sequence_parts).upper()))
            identifier = line[1:].split(maxsplit=1)[0]
            if not identifier:
                raise ValueError("FASTA record has an empty identifier")
            sequence_parts = []
            continue
        if identifier is None:
            raise ValueError("FASTA sequence appears before the first identifier")
        sequence_parts.append("".join(line.split()))
    if identifier is not None:
        records.append((identifier, "".join(sequence_parts).upper()))
    if not records:
        raise ValueError("FASTA reference has no records")
    return records


def normalize_fasta_reference(
    text: str, *, minimum_length: int = 8, maximum_length: int = 30
) -> tuple[str, FrozenFastaReference]:
    """Create a deterministic canonical reference without fitting to current candidates."""

    if minimum_length < 1 or maximum_length < minimum_length:
        raise ValueError("reference length domain is invalid")
    records = _parse_fasta(text)
    source_ids_by_sequence: dict[str, list[str]] = defaultdict(list)
    rejected_length = 0
    rejected_noncanonical = 0
    for source_id, sequence in records:
        if not sequence or set(sequence) - CANONICAL_AMINO_ACIDS:
            rejected_noncanonical += 1
            continue
        if not minimum_length <= len(sequence) <= maximum_length:
            rejected_length += 1
            continue
        source_ids_by_sequence[sequence].append(source_id)

    ordered = sorted(source_ids_by_sequence, key=lambda sequence: sha256_text(sequence))
    lines: list[str] = []
    accepted_records = 0
    for index, sequence in enumerate(ordered, start=1):
        source_ids = sorted(source_ids_by_sequence[sequence])
        accepted_records += len(source_ids)
        source_ids_sha256 = sha256_json(source_ids)
        lines.extend(
            [
                (
                    f">ampref_{index:06d}|sequence_sha256={sha256_text(sequence)}"
                    f"|source_count={len(source_ids)}|source_ids_sha256={source_ids_sha256}"
                ),
                sequence,
            ]
        )
    normalized_fasta = "\n".join(lines) + ("\n" if lines else "")
    sequence_set_sha256 = sha256_json(
        [
            {
                "sequence": sequence,
                "source_ids": sorted(source_ids_by_sequence[sequence]),
            }
            for sequence in ordered
        ]
    )
    witness = FrozenFastaReference(
        schema_version="ampgent.frozen-fasta-reference.1",
        source_record_count=len(records),
        accepted_record_count=accepted_records,
        unique_sequence_count=len(ordered),
        duplicate_record_count=accepted_records - len(ordered),
        rejected_length_count=rejected_length,
        rejected_noncanonical_count=rejected_noncanonical,
        minimum_length=minimum_length,
        maximum_length=maximum_length,
        normalized_fasta_sha256=sha256_text(normalized_fasta),
        sequence_set_sha256=sequence_set_sha256,
    )
    return normalized_fasta, witness


def require_candidate_independent_threshold_policy(policy: dict) -> None:
    """Fail closed if novelty/OOD thresholds were fitted on the candidates being ranked."""

    if policy.get("schema_version") != "ampgent.novelty-ood-threshold-policy.1":
        raise ValueError("novelty/OOD threshold policy schema is invalid")
    if policy.get("current_candidate_batch_used_for_threshold_fit") is not False:
        raise ValueError("current candidate batch must not be used to fit novelty/OOD thresholds")
    if policy.get("external_holdout_calibration_status") != "passed":
        raise ValueError("external holdout novelty/OOD calibration has not passed")
    artifact = policy.get("external_holdout_calibration_artifact_sha256")
    if not isinstance(artifact, str) or len(artifact) != 64 or any(
        character not in "0123456789abcdef" for character in artifact
    ):
        raise ValueError("external holdout calibration artifact SHA-256 is invalid")
