from __future__ import annotations

import asyncio
import os
import sys

from pepagent.workers.activities import _terminate_subprocess_tree


async def test_terminate_subprocess_tree_stops_running_process() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        start_new_session=os.name == "posix",
    )

    await _terminate_subprocess_tree(process)

    assert process.returncode is not None

