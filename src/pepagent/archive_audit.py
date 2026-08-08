from __future__ import annotations

import hashlib
import pickletools
import re
import stat
import zipfile
from collections import Counter, deque
from pathlib import Path, PurePosixPath
from typing import Any

SERIALIZED_MODEL_SUFFIXES = {".joblib", ".pickle", ".pkl", ".sav"}
SOURCE_SUFFIXES = {".bat", ".cmd", ".pl", ".ps1", ".py", ".sh"}
FORBIDDEN_SOURCE_PATTERNS = (
    (re.compile(r"(?<![\w.])eval\s*\("), "dynamic_eval", "eval("),
    (re.compile(r"(?<![\w.])exec\s*\("), "dynamic_exec", "exec("),
    (re.compile(r"os\.system\s*\("), "shell_execution", "os.system("),
    (re.compile(r"shell\s*=\s*true"), "shell_execution", "shell=True"),
)


def _sha256_stream(stream: Any) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _safe_member_name(
    raw_name: str, required_root: str, allowed_metadata_roots: tuple[str, ...]
) -> str:
    if "\\" in raw_name or "\x00" in raw_name:
        raise ValueError(f"unsafe ZIP member name: {raw_name!r}")
    path = PurePosixPath(raw_name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe ZIP member path: {raw_name!r}")
    normalized = path.as_posix()
    root_name = required_root.rstrip("/")
    root = root_name + "/"
    metadata_names = tuple(item.rstrip("/") for item in allowed_metadata_roots)
    metadata_roots = tuple(item + "/" for item in metadata_names)
    if normalized != root_name and normalized not in metadata_names and not normalized.startswith(
        (root, *metadata_roots)
    ):
        raise ValueError(f"ZIP member is outside required root {root!r}: {raw_name!r}")
    return normalized


def scan_pickle_opcodes(payload: bytes) -> dict[str, Any]:
    """Inspect pickle bytecode without importing or constructing its objects."""

    counts: Counter[str] = Counter()
    globals_seen: set[str] = set()
    recent_strings: deque[str] = deque(maxlen=2)
    for opcode, argument, _position in pickletools.genops(payload):
        counts[opcode.name] += 1
        if opcode.name in {"BINUNICODE", "SHORT_BINUNICODE", "UNICODE"}:
            recent_strings.append(str(argument))
        elif opcode.name == "GLOBAL":
            globals_seen.add(str(argument).replace(" ", ".", 1))
            recent_strings.clear()
        elif opcode.name == "STACK_GLOBAL":
            if len(recent_strings) == 2:
                globals_seen.add(f"{recent_strings[0]}.{recent_strings[1]}")
            else:
                globals_seen.add("<memoized-or-unresolved-stack-global>")
            recent_strings.clear()
    return {
        "opcode_counts": dict(sorted(counts.items())),
        "global_references": sorted(globals_seen),
    }


def audit_zip_archive(
    path: Path,
    *,
    required_root: str,
    allowed_metadata_roots: tuple[str, ...] = ("__MACOSX",),
) -> dict[str, Any]:
    archive_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    source_findings: list[dict[str, Any]] = []
    pickle_scans: list[dict[str, Any]] = []

    with zipfile.ZipFile(path) as archive:
        entry_count = len(archive.infolist())
        for info in archive.infolist():
            name = _safe_member_name(
                info.filename, required_root, allowed_metadata_roots
            )
            folded = name.casefold()
            if folded in seen:
                raise ValueError(f"duplicate ZIP member path: {name!r}")
            seen.add(folded)
            unix_mode = info.external_attr >> 16
            if stat.S_ISLNK(unix_mode):
                raise ValueError(f"symbolic links are forbidden in ZIP archives: {name!r}")
            if info.is_dir():
                continue
            with archive.open(info) as stream:
                payload = stream.read()
            digest = hashlib.sha256(payload).hexdigest()
            suffix = PurePosixPath(name).suffix.lower()
            rows.append(
                {"path": name, "size_bytes": info.file_size, "sha256": digest}
            )
            is_metadata = name.startswith(
                tuple(item.rstrip("/") + "/" for item in allowed_metadata_roots)
            )
            if suffix in SOURCE_SUFFIXES and not is_metadata:
                text = payload.decode("utf-8", errors="replace").lower()
                for pattern, finding, marker in FORBIDDEN_SOURCE_PATTERNS:
                    if pattern.search(text):
                        source_findings.append(
                            {"path": name, "finding": finding, "marker": marker}
                        )
            if suffix in SERIALIZED_MODEL_SUFFIXES and not is_metadata:
                pickle_scans.append(
                    {"path": name, "size_bytes": len(payload), **scan_pickle_opcodes(payload)}
                )

    rows.sort(key=lambda item: item["path"])
    inventory = "".join(
        f"{item['path']}\t{item['size_bytes']}\t{item['sha256']}\n" for item in rows
    ).encode()
    return {
        "archive_path": str(path),
        "archive_size_bytes": path.stat().st_size,
        "archive_sha256": archive_sha256,
        "entry_count": entry_count,
        "file_count": len(rows),
        "inventory_sha256": hashlib.sha256(inventory).hexdigest(),
        "files": rows,
        "source_findings": sorted(
            source_findings, key=lambda item: (item["path"], item["finding"])
        ),
        "pickle_scans": sorted(pickle_scans, key=lambda item: item["path"]),
    }
