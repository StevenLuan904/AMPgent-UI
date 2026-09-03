"""Copy only compact Pool-A MD evidence from a remote result tree."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

ALLOWED = {
    ("launch_receipt.json",),
    ("failure_receipt.json",),
    ("manifest.json",),
    ("analysis", "interface", "interface_analysis.json"),
    ("analysis", "interface", "postgresql_ingest_receipt.json"),
    ("analysis", "interface", "timeseries.csv"),
    ("analysis", "mmgbsa", "mmgbsa_analysis.json"),
    ("analysis", "mmgbsa", "postgresql_ingest_receipt.json"),
    ("analysis", "mmgbsa", "residue_decomposition_mean.csv"),
}
MUTABLE_FILENAMES = {"failure_receipt.json"}


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-target", required=True)
    parser.add_argument("--ssh-port", required=True, type=int)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--local-root", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--batch-mode", choices=("yes", "no"), default="yes")
    parser.add_argument("--refresh-existing", action="store_true")
    return parser.parse_args()


def allowed_relative_path(relative: PurePosixPath) -> bool:
    parts = relative.parts
    return len(parts) >= 3 and tuple(parts[2:]) in ALLOWED


def should_copy(relative: PurePosixPath, destination_exists: bool, refresh: bool) -> bool:
    return (
        refresh
        or not destination_exists
        or relative.name in MUTABLE_FILENAMES
    )


def remote_files(target: str, port: int, root: str, batch_mode: str = "yes") -> list[str]:
    names = " -o ".join(f'-name {shlex.quote(path[-1])}' for path in sorted(ALLOWED))
    command = f"find {shlex.quote(root)} -type f \\( {names} \\) -print"
    completed = subprocess.run(
        ["ssh", "-o", f"BatchMode={batch_mode}", "-p", str(port), target, command],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in completed.stdout.splitlines() if line]


def main() -> None:
    args = cli()
    remote_root = PurePosixPath(args.remote_root)
    copied = []
    existing = []
    ignored = []
    args.local_root.mkdir(parents=True, exist_ok=True)
    for source in remote_files(
        args.ssh_target, args.ssh_port, args.remote_root, args.batch_mode
    ):
        path = PurePosixPath(source)
        relative = path.relative_to(remote_root)
        if not allowed_relative_path(relative):
            ignored.append(relative.as_posix())
            continue
        destination = args.local_root.joinpath(*relative.parts)
        if not should_copy(relative, destination.is_file(), args.refresh_existing):
            existing.append(relative.as_posix())
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.part")
        subprocess.run(
            [
                "scp",
                "-o",
                f"BatchMode={args.batch_mode}",
                "-P",
                str(args.ssh_port),
                f"{args.ssh_target}:{source}",
                str(temporary),
            ],
            check=True,
        )
        temporary.replace(destination)
        copied.append(relative.as_posix())
    payload = {
        "schema_version": "ampgent.pool-a-md-compact-sync.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "remote_root": args.remote_root,
        "local_root": str(args.local_root.resolve()),
        "copied_file_count": len(copied),
        "copied_files": copied,
        "existing_immutable_file_count": len(existing),
        "existing_immutable_files": existing,
        "ignored_noncontract_file_count": len(ignored),
        "ignored_noncontract_files": ignored,
        "structure_or_trajectory_copied": False,
        "remote_files_deleted": False,
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
