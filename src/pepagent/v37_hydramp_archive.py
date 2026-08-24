from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from pepagent.provenance.hashing import sha256_json

# These limits are deliberately only modestly above the frozen upstream archive
# (9,783 members and 468,397,555 uncompressed bytes).  They are part of every
# inspection/materialization receipt so replay does not silently inherit a host
# or library default.
HYDRAMP_ARCHIVE_MAX_MEMBERS = 10_000
HYDRAMP_ARCHIVE_MAX_FILE_BYTES = 8 * 1024 * 1024
HYDRAMP_ARCHIVE_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
HYDRAMP_ARCHIVE_MAX_COMPRESSION_RATIO = 20.0
HYDRAMP_ARCHIVE_RESOURCE_LIMITS = {
    "maximum_member_count": HYDRAMP_ARCHIVE_MAX_MEMBERS,
    "maximum_file_bytes": HYDRAMP_ARCHIVE_MAX_FILE_BYTES,
    "maximum_uncompressed_bytes": HYDRAMP_ARCHIVE_MAX_UNCOMPRESSED_BYTES,
    "maximum_compression_ratio": HYDRAMP_ARCHIVE_MAX_COMPRESSION_RATIO,
}

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"\\|?*')
_PROCESS_VERIFIED_CACHE_ENTRIES: set[tuple[str, str]] = set()


@contextmanager
def _exclusive_file_lock(path: Path):
    """Hold one OS-released cross-process byte lock for cache publication."""
    _reject_existing_reparse_chain(path.parent)
    with path.open("a+b") as handle:
        _reject_existing_reparse_chain(path, stop=path.parent)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _sha256_open_file(source: BinaryIO) -> str:
    source.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    source.seek(0)
    return digest.hexdigest()


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _reject_existing_reparse_chain(path: Path, *, stop: Path | None = None) -> None:
    current = _absolute_lexical(path)
    boundary = _absolute_lexical(stop) if stop is not None else None
    if boundary is not None:
        try:
            current.relative_to(boundary)
        except ValueError as exc:
            raise ValueError("v37 HydrAMP materialization path escapes its work root") from exc
    while True:
        if current.exists() or current.is_symlink():
            metadata = current.lstat()
            attributes = getattr(metadata, "st_file_attributes", 0)
            if current.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(
                    "v37 HydrAMP materialization path contains a symlink or reparse point"
                )
        if current == boundary or current.parent == current:
            break
        current = current.parent


def _validate_windows_component(part: str) -> None:
    if unicodedata.normalize("NFC", part) != part:
        raise ValueError("v37 HydrAMP archive member is not NFC-normalized")
    if part.endswith((" ", ".")):
        raise ValueError("v37 HydrAMP archive member has a Windows-aliased suffix")
    if any(ord(character) < 32 or ord(character) == 127 for character in part):
        raise ValueError("v37 HydrAMP archive member contains a control character")
    if any(character in _WINDOWS_INVALID_CHARACTERS for character in part):
        raise ValueError("v37 HydrAMP archive member contains a Windows-invalid character")
    if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("v37 HydrAMP archive member uses a Windows reserved name")


def _walk_tree_no_reparse(root: Path) -> list[Path]:
    """Walk without following a link or junction before it has been rejected."""
    observed: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for child in directory.iterdir():
            _reject_existing_reparse_chain(child, stop=root)
            observed.append(child)
            if child.is_dir():
                pending.append(child)
    return observed


def _safe_members(bundle: zipfile.ZipFile) -> tuple[list[zipfile.ZipInfo], list[dict[str, Any]]]:
    members = sorted(bundle.infolist(), key=lambda item: item.filename)
    if len(members) > HYDRAMP_ARCHIVE_MAX_MEMBERS:
        raise ValueError("v37 HydrAMP model archive exceeds the member-count limit")
    exact: set[str] = set()
    folded: set[str] = set()
    kinds: dict[str, str] = {}
    inventory: list[dict[str, Any]] = []
    declared_uncompressed_bytes = 0
    for member in members:
        name = member.filename
        normalized_input = name.rstrip("/")
        parsed = PurePosixPath(normalized_input)
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        dos_attributes = member.external_attr & 0xFFFF
        if (
            not name
            or "\\" in name
            or parsed.is_absolute()
            or parsed.as_posix() != normalized_input
            or any(part in {"", ".", ".."} for part in parsed.parts)
            or member.flag_bits & 0x1
            or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            or dos_attributes & 0x400
            or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}
        ):
            raise ValueError("v37 HydrAMP model archive contains an unsafe member")
        for part in parsed.parts:
            _validate_windows_component(part)
        if not member.is_dir():
            if member.file_size > HYDRAMP_ARCHIVE_MAX_FILE_BYTES:
                raise ValueError("v37 HydrAMP archive member exceeds the size limit")
            declared_uncompressed_bytes += member.file_size
            if declared_uncompressed_bytes > HYDRAMP_ARCHIVE_MAX_UNCOMPRESSED_BYTES:
                raise ValueError("v37 HydrAMP archive exceeds the uncompressed-byte limit")
            if member.file_size and (
                member.compress_size == 0
                or member.file_size / member.compress_size
                > HYDRAMP_ARCHIVE_MAX_COMPRESSION_RATIO
            ):
                raise ValueError("v37 HydrAMP archive exceeds the compression-ratio limit")
        normalized = parsed.as_posix().rstrip("/")
        folded_name = normalized.casefold()
        if normalized in exact or folded_name in folded:
            raise ValueError("v37 HydrAMP model archive contains duplicate members")
        exact.add(normalized)
        folded.add(folded_name)
        kind = "directory" if member.is_dir() else "file"
        kinds[normalized] = kind
        inventory.append(
            {
                "path": normalized,
                "kind": kind,
                "size": member.file_size,
                "compressed_size": member.compress_size,
                "crc32": f"{member.CRC:08x}",
                "unix_mode": mode,
                "dos_attributes": dos_attributes,
            }
        )
    for name, _kind in kinds.items():
        parts = PurePosixPath(name).parts
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if kinds.get(parent) == "file":
                raise ValueError("v37 HydrAMP archive has a file/directory prefix collision")
    return members, inventory


def _inspect_open_archive(source: BinaryIO, *, archive_sha256: str) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    observed_total = 0
    with zipfile.ZipFile(source) as bundle:
        members, inventory = _safe_members(bundle)
        for member in members:
            if member.is_dir():
                continue
            digest = hashlib.sha256()
            observed_size = 0
            with bundle.open(member) as member_source:
                for chunk in iter(lambda: member_source.read(1024 * 1024), b""):
                    observed_size += len(chunk)
                    observed_total += len(chunk)
                    if (
                        observed_size > HYDRAMP_ARCHIVE_MAX_FILE_BYTES
                        or observed_total > HYDRAMP_ARCHIVE_MAX_UNCOMPRESSED_BYTES
                    ):
                        raise ValueError("v37 HydrAMP archive exceeded a streamed size limit")
                    digest.update(chunk)
            if observed_size != member.file_size:
                raise ValueError("v37 HydrAMP archive member size drifted")
            files.append(
                {
                    "path": PurePosixPath(member.filename).as_posix(),
                    "size": observed_size,
                    "sha256": digest.hexdigest(),
                }
            )
    return {
        "archive_sha256": archive_sha256,
        "member_inventory_sha256": sha256_json(inventory),
        "extracted_tree_sha256": sha256_json(files),
        "member_count": len(inventory),
        "file_count": len(files),
        "uncompressed_bytes": observed_total,
        "archive_resource_limits": dict(HYDRAMP_ARCHIVE_RESOURCE_LIMITS),
    }


def inspect_hydramp_archive(archive: Path) -> dict[str, Any]:
    with archive.open("rb") as source:
        before_sha256 = _sha256_open_file(source)
        contract = _inspect_open_archive(source, archive_sha256=before_sha256)
        after_sha256 = _sha256_open_file(source)
    if after_sha256 != before_sha256:
        raise ValueError("v37 HydrAMP model archive changed while being inspected")
    return contract


def cleanup_hydramp_materialization(path: Path, *, work: Path) -> None:
    work = _absolute_lexical(work)
    destination = _absolute_lexical(path)
    _reject_existing_reparse_chain(work)
    if destination.parent != work or not destination.name.startswith("hydramp-"):
        raise ValueError("v37 HydrAMP cleanup target is not a direct materialization child")
    _reject_existing_reparse_chain(destination, stop=work)
    if destination.exists():
        _walk_tree_no_reparse(destination)
        shutil.rmtree(destination)


def verify_hydramp_materialization(
    destination: Path,
    *,
    cache_root: Path,
    expected: dict[str, Any],
) -> dict[str, Any]:
    """Recompute the frozen tree identity before or after shared-cache use."""
    cache_root = _absolute_lexical(cache_root)
    destination = _absolute_lexical(destination)
    _reject_existing_reparse_chain(cache_root)
    if destination.parent != cache_root or not destination.name.startswith("hydramp-"):
        raise ValueError("v37 HydrAMP cache entry is not a direct cache child")
    _reject_existing_reparse_chain(destination, stop=cache_root)
    if not destination.is_dir():
        raise ValueError("v37 HydrAMP cache entry is missing")
    files: list[dict[str, Any]] = []
    observed_total = 0
    for path in sorted(_walk_tree_no_reparse(destination), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        observed_size = 0
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                observed_size += len(chunk)
                observed_total += len(chunk)
                if (
                    observed_size > HYDRAMP_ARCHIVE_MAX_FILE_BYTES
                    or observed_total > HYDRAMP_ARCHIVE_MAX_UNCOMPRESSED_BYTES
                ):
                    raise ValueError("v37 HydrAMP cached tree exceeded a size limit")
                digest.update(chunk)
        files.append(
            {
                "path": path.relative_to(destination).as_posix(),
                "size": observed_size,
                "sha256": digest.hexdigest(),
            }
        )
    tree_sha256 = sha256_json(files)
    if len(files) != expected["file_count"]:
        raise ValueError("v37 HydrAMP cached model inventory drifted")
    if observed_total != expected["uncompressed_bytes"]:
        raise ValueError("v37 HydrAMP cached model byte count drifted")
    if tree_sha256 != expected["extracted_tree_sha256"]:
        raise ValueError("v37 HydrAMP cached model tree drifted")
    return {
        "file_count": len(files),
        "uncompressed_bytes": observed_total,
        "extracted_tree_sha256": tree_sha256,
    }


def materialize_hydramp_archive_cached(
    archive: Path,
    *,
    cache_root: Path,
    expected: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    """Publish one immutable, content-addressed extraction for repeated cells.

    Contenders extract into private temporary directories.  A rename publishes a
    complete tree atomically; losing contenders discard their private tree and
    verify the winner before reuse.
    """
    cache_root = _absolute_lexical(cache_root)
    _reject_existing_reparse_chain(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    _reject_existing_reparse_chain(cache_root)
    destination = cache_root / f"hydramp-{expected['archive_sha256']}"
    lock_path = cache_root / f".{destination.name}.lock"
    with _exclusive_file_lock(lock_path):
        cache_hit = destination.exists()
        source_receipt: dict[str, Any] | None = None
        process_key = (str(destination), str(expected["extracted_tree_sha256"]))
        if not cache_hit:
            contender, source_receipt = materialize_hydramp_archive(
                archive,
                work=cache_root,
                expected=expected,
            )
            contender.rename(destination)
            verified = {
                "file_count": source_receipt["file_count"],
                "uncompressed_bytes": source_receipt["uncompressed_bytes"],
                "extracted_tree_sha256": source_receipt["extracted_tree_sha256"],
            }
            _PROCESS_VERIFIED_CACHE_ENTRIES.add(process_key)
            cache_validation = "cold_publish_full_tree"
        elif process_key not in _PROCESS_VERIFIED_CACHE_ENTRIES:
            verified = verify_hydramp_materialization(
                destination,
                cache_root=cache_root,
                expected=expected,
            )
            _PROCESS_VERIFIED_CACHE_ENTRIES.add(process_key)
            cache_validation = "first_use_full_tree"
        else:
            verified = {
                "file_count": expected["file_count"],
                "uncompressed_bytes": expected["uncompressed_bytes"],
                "extracted_tree_sha256": expected["extracted_tree_sha256"],
            }
            cache_validation = "process_memoized_frozen_identity"
    receipt = {
        "schema_version": "v37.hydramp-materialization-cache-receipt.1",
        "archive_sha256": expected["archive_sha256"],
        "member_inventory_sha256": expected["member_inventory_sha256"],
        "extracted_tree_sha256": verified["extracted_tree_sha256"],
        "member_count": expected["member_count"],
        "file_count": verified["file_count"],
        "uncompressed_bytes": verified["uncompressed_bytes"],
        "archive_resource_limits": dict(HYDRAMP_ARCHIVE_RESOURCE_LIMITS),
        "cache_entry_name": destination.name,
        "cache_hit": cache_hit,
        "cache_validation": cache_validation,
        "source_materialization_receipt_sha256": (
            source_receipt["materialization_receipt_sha256"]
            if source_receipt is not None
            else None
        ),
    }
    receipt["materialization_receipt_sha256"] = sha256_json(receipt)
    return destination, receipt


def materialize_hydramp_archive(
    archive: Path,
    *,
    work: Path,
    expected: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    if expected.get("archive_resource_limits") != HYDRAMP_ARCHIVE_RESOURCE_LIMITS:
        raise ValueError("v37 HydrAMP archive resource limits drifted")
    work = _absolute_lexical(work)
    _reject_existing_reparse_chain(work)
    work.mkdir(parents=True, exist_ok=True)
    _reject_existing_reparse_chain(work)
    destination = Path(
        tempfile.mkdtemp(
            prefix=f"hydramp-{expected['archive_sha256'][:16]}-",
            dir=work,
        )
    )
    _reject_existing_reparse_chain(destination, stop=work)
    try:
        files: list[dict[str, Any]] = []
        observed_total = 0
        with archive.open("rb") as archive_source:
            before_sha256 = _sha256_open_file(archive_source)
            if before_sha256 != expected["archive_sha256"]:
                raise ValueError("v37 HydrAMP model archive bytes drifted")
            with zipfile.ZipFile(archive_source) as bundle:
                members, inventory = _safe_members(bundle)
                if sha256_json(inventory) != expected["member_inventory_sha256"]:
                    raise ValueError("v37 HydrAMP archive member inventory drifted")
                for member in members:
                    relative = PurePosixPath(member.filename)
                    target = destination.joinpath(*relative.parts)
                    _reject_existing_reparse_chain(target.parent, stop=destination)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=False)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _reject_existing_reparse_chain(target.parent, stop=destination)
                    digest = hashlib.sha256()
                    observed_size = 0
                    with bundle.open(member) as member_source, target.open("xb") as output:
                        for chunk in iter(lambda: member_source.read(1024 * 1024), b""):
                            observed_size += len(chunk)
                            observed_total += len(chunk)
                            if (
                                observed_size > HYDRAMP_ARCHIVE_MAX_FILE_BYTES
                                or observed_total > HYDRAMP_ARCHIVE_MAX_UNCOMPRESSED_BYTES
                            ):
                                raise ValueError(
                                    "v37 HydrAMP extraction exceeded a streamed size limit"
                                )
                            output.write(chunk)
                            digest.update(chunk)
                    if observed_size != member.file_size:
                        raise ValueError("v37 HydrAMP extracted member size drifted")
                    files.append(
                        {
                            "path": relative.as_posix(),
                            "size": observed_size,
                            "sha256": digest.hexdigest(),
                        }
                    )
            after_sha256 = _sha256_open_file(archive_source)
        if after_sha256 != before_sha256:
            raise ValueError("v37 HydrAMP model archive changed while being extracted")
        observed_files: set[str] = set()
        for path in _walk_tree_no_reparse(destination):
            if path.is_file():
                observed_files.add(path.relative_to(destination).as_posix())
        expected_files = {item["path"] for item in files}
        if observed_files != expected_files:
            raise ValueError("v37 HydrAMP materialized model inventory drifted")
        tree_sha256 = sha256_json(files)
        if tree_sha256 != expected["extracted_tree_sha256"]:
            raise ValueError("v37 HydrAMP extracted tree bytes drifted")
        receipt = {
            "schema_version": "v37.hydramp-materialization-receipt.1",
            "archive_sha256": expected["archive_sha256"],
            "member_inventory_sha256": expected["member_inventory_sha256"],
            "extracted_tree_sha256": tree_sha256,
            "member_count": len(inventory),
            "file_count": len(files),
            "uncompressed_bytes": observed_total,
            "archive_resource_limits": dict(HYDRAMP_ARCHIVE_RESOURCE_LIMITS),
        }
        receipt["materialization_receipt_sha256"] = sha256_json(receipt)
        return destination, receipt
    except BaseException:
        cleanup_hydramp_materialization(destination, work=work)
        raise
