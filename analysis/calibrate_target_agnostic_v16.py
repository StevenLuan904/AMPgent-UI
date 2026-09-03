from __future__ import annotations

import os
import runpy

os.environ["AMPGENT_GENERATION"] = "16"
source = str(__file__).replace(
    "calibrate_target_agnostic_v16.py", "calibrate_target_agnostic_v15.py"
)
runpy.run_path(source, run_name="__main__")
