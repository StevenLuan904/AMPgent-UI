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
    recent_symbols: deque[str] = deque(maxlen=2)
    memo: dict[int, str | None] = {}
    current_symbol: str | None = None
    for opcode, argument, _position in pickletools.genops(payload):
        counts[opcode.name] += 1
        if opcode.name in {"BINUNICODE", "SHORT_BINUNICODE", "UNICODE"}:
            current_symbol = str(argument)
            recent_symbols.append(current_symbol)
        elif opcode.name == "MEMOIZE":
            memo[len(memo)] = current_symbol
        elif opcode.name in {"BINPUT", "LONG_BINPUT", "PUT"}:
            memo[int(argument)] = current_symbol
        elif opcode.name in {"BINGET", "LONG_BINGET", "GET"}:
            current_symbol = memo.get(int(argument))
            if current_symbol is None:
                recent_symbols.clear()
            else:
                recent_symbols.append(current_symbol)
        elif opcode.name == "GLOBAL":
            current_symbol = str(argument).replace(" ", ".", 1)
            globals_seen.add(current_symbol)
            recent_symbols.clear()
        elif opcode.name == "STACK_GLOBAL":
            if len(recent_symbols) == 2:
                current_symbol = f"{recent_symbols[0]}.{recent_symbols[1]}"
                globals_seen.add(current_symbol)
            else:
                current_symbol = None
                globals_seen.add("<unresolved-stack-global>")
            recent_symbols.clear()
        elif opcode.name not in {"PROTO", "FRAME"}:
            current_symbol = None
            recent_symbols.clear()
    return {
        "opcode_counts": dict(sorted(counts.items())),
        "global_references": sorted(globals_seen),
    }


def audit_zip_archive(
    path: Path,
    *,
    required_root: str,
    allowed_metadata_roots: tuple[str, ...] = ("__MACOSX",),
    allowed_pickle_globals: frozenset[str] | None = None,
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
                scan = scan_pickle_opcodes(payload)
                references = set(scan["global_references"])
                if "<unresolved-stack-global>" in references:
                    raise ValueError(f"unresolved pickle global reference in {name!r}")
                if allowed_pickle_globals is not None:
                    unexpected = references - allowed_pickle_globals
                    if unexpected:
                        raise ValueError(
                            f"unexpected pickle globals in {name!r}: "
                            + ", ".join(sorted(unexpected))
                        )
                pickle_scans.append(
                    {"path": name, "size_bytes": len(payload), **scan}
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
