"""Start the physicochemical runtime under ``python -S`` without reading .pth files."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def _site_packages_from_executable() -> Path:
    executable = Path(sys.executable).resolve()
    site_packages = executable.parent.parent / "Lib" / "site-packages"
    if not site_packages.is_dir():
        raise RuntimeError("physicochemical no-site bootstrap cannot locate site-packages")
    return site_packages


def main() -> None:
    # Do not call site.main()/addsitedir(): both process .pth files, which is the
    # Windows startup boundary this adapter is designed to avoid.
    sys.path.insert(0, str(_site_packages_from_executable()))
    runtime = Path(__file__).resolve().with_name("cli.py")
    runpy.run_path(str(runtime), run_name="__main__")


if __name__ == "__main__":
    main()
