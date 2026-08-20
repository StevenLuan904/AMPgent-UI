# AMPgent v38 recovery-013 progress review

Observed at: 2026-08-20 07:35 Asia/Shanghai

## Immutable run identity

- Controller run: `3a714c90-7f69-43d4-95df-0fe24ed47247`
- Science run: `57afecc7-22e9-4efb-9051-acb11234013d`
- Workflow: `pepagent-sequence-first-v38-6e0066bad58189b9dd1cb07d1dba30771faa0fe081a48ba6a5dc8c954f60e47c`
- Temporal run: `9a838f66-462f-4337-9476-1b5f0b4c59cd`
- Status: running; stage `parallel_target_structure`

## Sequence-first completion

- Raw occurrences: 900 / 900
- Valid unique candidates: 773
- Sequence evaluations: 8,503 / 8,503 (`773 x 11`)
- Admission decisions: 1 immutable decision over all 773 candidates
- Mature core: 26
- Promising but uncertain: 124, of which 9 entered the frozen exploration budget
- Rejected by the frozen evidence policy: 623
- Structure-eligible union: 35 (`26 mature + 9 exploration`)
- Unused structure slots: 13; no forced fill
- Refinement was not triggered because the mature core exceeded the frozen minimum of 12.

All 26 mature-core candidates passed the two hard label gates: MACREL hemolysis label `low` and ToxinPred3 label `Non-Toxin`. MIC/activity/developability were retained as non-weighted Pareto objectives rather than hard externally chosen cutoffs. These candidates remain computationally provisional until both target branches and replay close.

## Structure progress and workload

Frozen workload for the 35 admitted candidates:

- 2 isolated targets
- native and wrong-pocket lanes per target
- 3 Boltz seeds per candidate/lane/target
- 16 Rosetta decoys per pose
- Total structure tasks: `35 x 2 x 2 x 3 = 420`
- Total expected evidence records: `420 x (1 Boltz pose + 16 Rosetta decoys) = 7,140`

Current durable structure evidence:

- Completed tasks: 10 / 420 (2.38%)
- Evidence records: 170 / 7,140 (2.38%)
- E. coli GyrA native: 6 Boltz poses + 96 Rosetta decoys
- E. coli GyrA wrong-pocket: 4 Boltz poses + 64 Rosetta decoys
- S. epidermidis PBP2a: not yet durably reached by the deterministic dispatch order
- Replay evidence: 0; final Pareto/portfolio is therefore not yet available

Temporal remained `RUNNING`. At this observation, two new `predict_v38_multitarget_structure` activities were pending at attempt 1: one started and heartbeating, one scheduled behind the single authorized Boltz GPU poller. No retry or failure drift was observed.

## Throughput diagnosis and next control action

The science pipeline is advancing, but the resource bottleneck has moved from sequence metrics to structure throughput. `structure_concurrency=2` creates two in-flight activities, while the exact run currently has one authorized Boltz GPU worker and one Rosetta producer. Adding an unbound worker mid-run would alter the frozen execution/evidence identity, so this run must continue with its preflight-bound placement. The controller must treat a missing five-minute durable increment as a stall signal, distinguish a live heartbeat from a wedged activity, and only repair ordinary engineering faults without changing the exact run identity.

Next critical path: finish all 420 target/control/seed tasks, verify exactly 7,140 content-addressed structure records, then create the target-agnostic, per-target, and cross-target Pareto portfolios and database/object-store replay.

## Framework audit performed during this review

The absence of PBP2a records exposed a real scheduling defect rather than a scientific result. The planner emitted all GyrA tasks before all PBP2a tasks, while the workflow consumed consecutive bounded batches. Consequently, `max_parallel_targets=2` produced two concurrent tasks from the same target instead of one task from each target. The active immutable run is not modified. The framework task planner was changed for future identities to interleave targets within every `parallel_wave`; its focused Ruff check and 26 workflow/planner tests passed. This preserves candidate, target, seed, lane, and decoy budgets while making the declared multi-target parallelism operational.

## 09:30 checkpoint and second scheduling correction

- Durable structure evidence increased from 170 to 340 records: 20 / 420 tasks complete.
- All 57 current ToolCalls succeeded; the workflow remained running with two attempt-1 Rosetta activities actively heartbeating.
- All six required queue roles had their expected poller identities.
- The sequence pool remained unchanged at 26 mature-core plus 9 exploration candidates; no final champion or replay exists yet.

The live trace also exposed a second future-framework inefficiency: the workflow placed a batch barrier around two complete Boltz-to-Rosetta chains. While both chains were in Rosetta, the authorized GPU had no next Boltz activity to run. Future workflow identities now use independent bounded semaphores for Boltz and Rosetta, allowing the next pose inference to overlap prior CPU scoring. The active run remains on its immutable loaded workflow code.
