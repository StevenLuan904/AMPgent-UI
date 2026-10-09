import argparse
import csv
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round-dir", type=Path, required=True)
    ap.add_argument("--archive", type=Path, required=True)
    ap.add_argument("--round", type=int, required=True)
    args = ap.parse_args()
    d = args.round_dir
    tag = f"r{args.round}"
    rows = []
    for target in ("acea", "vegfa"):
        req = json.loads((d / f"{tag}_{target}_request.json").read_text(encoding="utf-8"))
        if int(req.get("proposal_round", -1)) != args.round:
            raise ValueError(f"request proposal_round does not match --round for {target}")
        out_path = d / f"{tag}_{target}_output.json"
        out = json.loads(out_path.read_text(encoding="utf-8"))
        plans = {p["action_id"]: p for p in req["action_plans"]}
        for cand in out["candidates"]:
            plan = plans[cand["action_id"]]
            rows.append({
                "candidate_id": cand["action_id"],
                "authoritative_candidate_id": cand["action_id"],
                "action_id": cand["action_id"],
                "action_sha256": cand["action_sha256"],
                "target": target,
                "target_key": target,
                "sequence": cand["sequence"],
                "parent_candidate_id": plan["parent_authoritative_candidate_id"],
                "parent_sequence": cand["parent_sequence"],
                "parent_generation": plan["parent_lineage_generation"],
                "lineage_generation": plan["lineage_generation"],
                "mutation_positions": json.dumps(cand["mutation_positions"], separators=(",", ":")),
                "seed": cand["seed"],
                "action_seed": cand["action_seed"],
                "conditional_nll": cand["conditional_nll"],
                "conditional_ppl": cand["conditional_ppl"],
                "model_revision": out["revision"],
                "device": out["device"],
                "source_output": str(out_path),
                "source_request": str(d / f"{tag}_{target}_request.json"),
                "source_output_sha256": sha256(out_path),
            })

    fields = list(rows[0])
    raw_path = d / f"{tag}_generated_raw_occurrences.csv"
    with raw_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

    archive_exists = args.archive.exists()
    oldseq = set()
    if archive_exists:
        with args.archive.open(newline="", encoding="utf-8-sig") as f:
            oldseq = {r.get("sequence") for r in csv.DictReader(f)}
    canonical = {}
    aliases = []
    for row in rows:
        key = row["sequence"]
        cid = canonical.setdefault(key, row["candidate_id"])
        aliases.append({"raw_candidate_id": row["candidate_id"], "canonical_unique_candidate_id": cid,
                        "target": row["target"], "sequence": row["sequence"],
                        "within_batch_duplicate": cid != row["candidate_id"],
                        "historical_replay": (row["sequence"] in oldseq) if archive_exists else None})
    score_rows = [r for r in rows if r["candidate_id"] == canonical[r["sequence"]]]
    score_path = d / f"{tag}_generated_score_input.csv"
    score_fields = ["candidate_id", "action_id", "authoritative_candidate_id", "target", "sequence",
                    "parent_candidate_id", "parent_sequence", "parent_generation", "lineage_generation",
                    "seed", "mutation_positions", "action_sha256", "conditional_nll", "conditional_ppl",
                    "model_revision", "source_output", "source_request", "source_output_sha256"]
    with score_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=score_fields, extrasaction="ignore")
        w.writeheader(); w.writerows(score_rows)
    (d / f"{tag}_raw_to_unique_alias_map.json").write_text(json.dumps({"raw_count": len(rows),
        "unique_count": len(score_rows), "aliases": aliases}, indent=2, sort_keys=True), encoding="utf-8")

    historical = [{"candidate_id": r["candidate_id"], "target": r["target"],
                   "sequence": r["sequence"], "historical_sequence_match": (r["sequence"] in oldseq) if archive_exists else None}
                  for r in rows]
    (d / f"{tag}_historical_duplicate_audit.json").write_text(json.dumps({"archive": str(args.archive),
        "archive_exists": archive_exists, "historical_check_status": "checked" if archive_exists else "unavailable",
        "raw_count": len(rows), "unique_count": len(score_rows),
        "within_batch_duplicate_count": len(rows)-len(score_rows), "rows": historical}, indent=2, sort_keys=True), encoding="utf-8")
    claims = [json.loads((d / f"{tag}_{t}_claim.json").read_text(encoding="utf-8")) for t in ("acea", "vegfa")]
    if any(int(c.get("proposal_round", -1)) != args.round for c in claims):
        raise ValueError("claim proposal_round does not match --round")
    (d / f"{tag}_generation_terminal_receipt.json").write_text(json.dumps({"round": args.round, "claims": claims,
        "raw_output_paths": [str(d / f"{tag}_acea_output.json"), str(d / f"{tag}_vegfa_output.json")],
        "raw_output_sha256": {"acea": sha256(d / f"{tag}_acea_output.json"), "vegfa": sha256(d / f"{tag}_vegfa_output.json")}}, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"raw_count": len(rows), "unique_count": len(score_rows),
                      "within_batch_duplicate_count": len(rows)-len(score_rows),
                      "raw": str(raw_path), "score": str(score_path)}))


if __name__ == "__main__":
    main()
