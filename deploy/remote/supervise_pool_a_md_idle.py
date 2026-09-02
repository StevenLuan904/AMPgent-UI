"""Continuously dispatch an immutable Pool-A MD queue to strictly idle GPUs."""

from __future__ import annotations

import argparse, json, os, subprocess, time
from pathlib import Path


def cli():
    p=argparse.ArgumentParser(); p.add_argument("--snapshot",type=Path,required=True)
    p.add_argument("--root",type=Path,required=True); p.add_argument("--runner",type=Path,required=True)
    p.add_argument("--python",required=True); p.add_argument("--gpu-indices",default="0,1,2,3,4,5,6,7")
    p.add_argument("--scan-root",action="append",required=True); p.add_argument("--poll-seconds",type=int,default=120)
    return p.parse_args()


def idle(gpu:int)->bool:
    q=subprocess.run(["nvidia-smi","-i",str(gpu),"--query-gpu=memory.used,utilization.gpu",
        "--format=csv,noheader,nounits"],capture_output=True,text=True)
    if q.returncode: return False
    mem,util=[int(v.strip()) for v in q.stdout.strip().split(",")]
    if mem>256 or util>5: return False
    for proc in Path("/proc").glob("[0-9]*"):
        try: env=(proc/"environ").read_bytes().split(b"\0")
        except OSError: continue
        if f"CUDA_VISIBLE_DEVICES={gpu}".encode() in env: return False
    return True


def build_index(roots):
    index={}
    for root in roots:
        for result in Path(root).rglob("rosetta_result.json"):
            parts=result.parts
            try:
                i=parts.index("candidates")
                key=(parts[i+1],parts[i+2])
            except (ValueError, IndexError):
                continue
            index.setdefault(key,[]).append(result)
    return index


def source_for(task, index):
    hits=index.get((task["target_key"],task["sequence_sha256"]),[])
    if not hits: return None
    result=max(hits,key=lambda p:p.stat().st_mtime)
    data=json.loads(result.read_text())
    # Rosetta JSON paths are relative to work/rosetta_coarse5; the compact
    # published JSON itself lives under protocols/coarse5/results.
    return result.parents[3] / "work" / "rosetta_coarse5" / data["best_decoy"]["structure"]


def main():
    a=cli(); a.root.mkdir(parents=True,exist_ok=True)
    snapshot=json.loads(a.snapshot.read_text()); tasks=snapshot["pool_a_all"]
    index=build_index(a.scan_root)
    pending=[]
    for rank,t in enumerate(tasks,1):
        out=a.root/t["target_key"]/t["candidate_id"]
        if (out/"manifest.json").exists(): continue
        src=source_for(t,index)
        if src: pending.append((rank,t,src,out))
    state={"schema_version":"ampgent.pool-a-md-supervisor.1","snapshot":str(a.snapshot),
           "snapshot_candidate_count":len(tasks),"resolvable_count":len(pending),"pid":os.getpid()}
    (a.root/"supervisor.json").write_text(json.dumps(state,indent=2))
    active={}; gpus=[int(x) for x in a.gpu_indices.split(",")]
    while pending or active:
        for gpu,p in list(active.items()):
            if p.poll() is not None: del active[gpu]
        for gpu in gpus:
            if not pending or gpu in active or not idle(gpu): continue
            rank,t,src,out=pending.pop(0); out.mkdir(parents=True,exist_ok=True)
            receipt={"candidate_id":t["candidate_id"],"run_id":t["run_id"],"target_key":t["target_key"],
                     "sequence_sha256":t["sequence_sha256"],"input_pdb":str(src),"gpu":gpu,"snapshot_rank":rank}
            (out/"launch_receipt.json").write_text(json.dumps(receipt,indent=2))
            log=(out/"run.log").open("a")
            env=os.environ.copy(); env["CUDA_VISIBLE_DEVICES"]=str(gpu)
            cmd=[a.python,str(a.runner),"--input-pdb",str(src),"--output-dir",str(out),
                 "--gpu-index","0","--seed",str(20260903+rank)]
            active[gpu]=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
        time.sleep(a.poll_seconds)

if __name__=="__main__": main()
