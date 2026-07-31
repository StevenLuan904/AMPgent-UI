"""Historical PepPAP reproduction adapter.

This module is intentionally not registered with a workflow or worker. It is retained only so the
project's existing validation evidence remains explainable. PepPAP failed the scientific admission
decision and must not be used for new experiments.
"""

import argparse
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PK_PATTERN = re.compile(r"predicted pK value is:\s*\[?(-?\d+(?:\.\d+)?)")
DG_PATTERN = re.compile(r"predicted dg \(kcal/mol\) value is:\s*\[?(-?\d+(?:\.\d+)?)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upstream-repo", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="peppap-") as temp_dir:
        work = Path(temp_dir) / "PepPAP"
        shutil.copytree(args.upstream_repo, work)
        run_script = work / "run.sh"
        script = run_script.read_text(encoding="utf-8", errors="replace")
        script = re.sub(
            r"^proseq=.*$", f"proseq='{request['target_sequence']}'", script, flags=re.M
        )
        script = re.sub(
            r"^pepseq=.*$", f"pepseq='{request['peptide_sequence']}'", script, flags=re.M
        )
        run_script.write_text(script, encoding="utf-8", newline="\n")
        completed = subprocess.run(
            ["bash", "run.sh"], cwd=work, check=False, capture_output=True, text=True
        )
        combined = f"{completed.stdout}\n{completed.stderr}"
        if completed.returncode != 0:
            raise RuntimeError(f"PepPAP failed ({completed.returncode}): {combined[-4000:]}")
        pk_match = PK_PATTERN.search(combined)
        dg_match = DG_PATTERN.search(combined)
        if not pk_match:
            raise ValueError(f"PepPAP output could not be parsed: {combined[-2000:]}")
        pk = float(pk_match.group(1))
        dg = float(dg_match.group(1)) if dg_match else -(pk * 0.59 * math.log(10))
        result = {
            "schema_version": "1.0",
            "evaluator": "PepPAP",
            "predicted_pkd_pki": pk,
            "derived_kd_molar": 10 ** (-pk),
            "predicted_delta_g_kcal_mol": dg,
            "status": "experimental",
            "out_of_domain": bool(request.get("out_of_domain", False)),
            "limitations": [
                "Model predicts a mixed pKd/pKi target and cannot distinguish Kd from Ki",
                "Absolute calibration on a new target family is unverified",
                "Upstream implementation and checkpoints require independent reproduction",
            ],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
