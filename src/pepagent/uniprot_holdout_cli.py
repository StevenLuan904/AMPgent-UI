from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from pepagent.novelty_reference import normalize_fasta_reference, parse_fasta_records

NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _write_bytes_atomic(path, (json.dumps(payload, indent=2) + "\n").encode())


def _fetch_page(client: httpx.Client, url: str, *, maximum_attempts: int = 5) -> httpx.Response:
    for attempt in range(1, maximum_attempts + 1):
        try:
            response = client.get(url)
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                return response
        except httpx.HTTPError:
            if attempt == maximum_attempts:
                raise
        if attempt == maximum_attempts:
            response.raise_for_status()
        time.sleep(2 ** (attempt - 1))
    raise RuntimeError("unreachable retry state")


def fetch_paginated_fasta(
    *, query: str, page_size: int = 500
) -> tuple[bytes, list[dict[str, Any]], dict[str, str | int | None]]:
    if page_size < 1 or page_size > 500:
        raise ValueError("UniProt page size must be between 1 and 500")
    url = "https://rest.uniprot.org/uniprotkb/search?" + urlencode(
        {
            "query": query,
            "format": "tsv",
            "fields": "accession,sequence,length,keyword",
            "size": page_size,
        }
    )
    pages: list[bytes] = []
    page_witnesses: list[dict[str, Any]] = []
    release: str | None = None
    release_date: str | None = None
    expected_total: int | None = None
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        while url:
            response = _fetch_page(client, url)
            payload = response.content
            reader = csv.DictReader(io.StringIO(payload.decode("utf-8")), delimiter="\t")
            records: list[tuple[str, str]] = []
            for row in reader:
                accession = str(row.get("Entry") or "").strip()
                sequence = str(row.get("Sequence") or "").strip().upper()
                declared_length = int(str(row.get("Length") or "0"))
                if not accession or not sequence or len(sequence) != declared_length:
                    raise ValueError("UniProt TSV row is incomplete or has a length mismatch")
                records.append((f"sp|{accession}|", sequence))
            if not records:
                raise ValueError("UniProt TSV page has no records")
            page_witnesses.append(
                {
                    "page_no": len(page_witnesses) + 1,
                    "record_count": len(records),
                    "bytes": len(payload),
                    "sha256": _sha256_bytes(payload),
                    "wire_format": "tsv",
                }
            )
            page_fasta = "".join(
                f">{identifier}\n{sequence}\n" for identifier, sequence in records
            ).encode()
            pages.append(page_fasta)
            if expected_total is None:
                total = response.headers.get("x-total-results")
                expected_total = int(total) if total is not None else None
                release = response.headers.get("x-uniprot-release")
                release_date = response.headers.get("x-uniprot-release-date")
            link = response.headers.get("link", "")
            match = NEXT_LINK.search(link)
            url = match.group(1) if match else ""
    raw = b"".join(pages)
    observed_total = len(parse_fasta_records(raw.decode("utf-8")))
    if expected_total is None or observed_total != expected_total:
        raise ValueError(
            f"UniProt result count mismatch: expected {expected_total}, observed {observed_total}"
        )
    return raw, page_witnesses, {
        "release": release,
        "release_date": release_date,
        "expected_total_results": expected_total,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze an external UniProt peptide holdout")
    parser.add_argument("--query", required=True)
    parser.add_argument("--output-raw", type=Path, required=True)
    parser.add_argument("--output-normalized", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    raw, pages, headers = fetch_paginated_fasta(query=args.query)
    normalized, normalization = normalize_fasta_reference(raw.decode("utf-8"))
    raw_sha256 = _sha256_bytes(raw)
    normalized_payload = normalized.encode()
    _write_bytes_atomic(args.output_raw, raw)
    _write_bytes_atomic(args.output_normalized, normalized_payload)
    manifest = {
        "schema_version": "ampgent.uniprot-external-holdout-manifest.1",
        "observed_at": datetime.now(UTC).isoformat(),
        "source": {
            "provider": "UniProtKB/Swiss-Prot",
            "api": "https://rest.uniprot.org/uniprotkb/search",
            "query": args.query,
            "wire_format": "tsv",
            "fields": ["accession", "sequence", "length", "keyword"],
            "license": "CC-BY-4.0",
            **headers,
        },
        "raw": {
            "record_count": headers["expected_total_results"],
            "bytes": len(raw),
            "sha256": raw_sha256,
            "pages": pages,
        },
        "normalization": normalization.to_dict(),
        "candidate_data_used": False,
        "label_semantics": (
            "reviewed 8-30-aa UniProtKB entries lacking the Antimicrobial keyword; "
            "this is a negative-distribution holdout, not proof that every entry is inactive"
        ),
        "limitations": [
            "Keyword exclusion can retain unannotated antimicrobial peptides and peptide toxins.",
            "The holdout is not matched to a specific pathogen, assay, or concentration endpoint.",
            "It may calibrate distributional novelty but cannot establish antimicrobial activity.",
        ],
    }
    _write_json_atomic(args.output_manifest, manifest)
    print(json.dumps(manifest, separators=(",", ":")))


if __name__ == "__main__":
    main()
