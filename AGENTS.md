# Codex Delivery and Versioning Rules

These rules apply to every Codex change in this repository.

## Mandatory AMPgent/AceA execution protocol

Before any AMPgent/AceA research, deployment, monitoring, or formal-run action, read
`docs/ampgent-acea-execution-protocol.md` completely. Its current-state ledger is the operational
source of truth for frozen versions, host restrictions, run limits, evidence semantics, and the
next authorized action. Exact benchmark/config files remain the scientific protocol contracts.

Before planning a new major version, changing the Agent harness, or claiming that a research
question is solved, also read and maintain `docs/ampgent-long-horizon-goals.zh-CN.md`. That document
is the append-only problem ledger for charge design, search sufficiency, knowledge/PepShot
interventions, multi-target generalization, and champion/challenger harness evolution. A roadmap
entry never substitutes for a frozen benchmark contract or formal-run authorization.

If memory, a heartbeat prompt, or an old handoff conflicts with the execution protocol, stop and
reconcile the repository evidence before acting. Never infer worker location merely from a Temporal
poller identity, and never submit a formal run until the worker host and loaded source revision are
verified.

## Git workflow

- The canonical remote is `https://github.com/StevenLuan904/AMPgent.git`.
- `main` is the releasable branch. Do not develop directly on it after the initial repository
  bootstrap.
- Use short-lived branches named `agent/<scope>`; keep each branch focused on one coherent change.
- Commit and push at meaningful checkpoints. Do not leave substantial reproducible work only on one
  machine.
- Before committing, inspect `git status` and the staged diff. Never commit credentials, `.env`,
  machine-specific configuration, model weights, databases, caches, or generated experiment output.
- Use concise commit subjects. Never force-push shared branches or rewrite published history.
- Run `ruff check src tests` and `pytest -q` before merging. Record any unavoidable skipped check.

## GitHub access

- Prefer GitHub CLI (`gh`) for authentication, pull requests, and GitHub Release objects.
- If `winget` and `gh` are unavailable, use Git for Windows with Git Credential Manager for normal
  `git push` and tag publication. Browser authentication is acceptable.
- Never read, print, persist, or repurpose a credential-manager token for direct API calls.
- A connected GitHub app may not have access to a private repository. Verify the actual Git remote
  with `git remote -v`, `git ls-remote`, and the pushed commit rather than assuming connector access.
- An annotated Git tag is a valid source version but is not the same object as a GitHub Releases
  page. Create the latter with authenticated `gh` or the GitHub UI when required, and report the
  distinction explicitly.
- Do not block source commits or branch pushes merely because Release/PR convenience tooling is
  absent. Push the recoverable Git state first, then finish the GitHub UI object when access permits.

## Releases

- Use semantic versions. The current MVP baseline is `v0.1.0`.
- Tag releases only from a tested `main` commit. Release notes must state scope, validation, model or
  source revisions, schema changes, and known scientific limitations.
- Store large model assets and run evidence in the model registry/object store, not GitHub or Git
  LFS. Git records their immutable identifiers and SHA-256 hashes.

## Large-data placement and local-disk discipline

- Keep the local repository and workstation focused on code, frozen configs, manifests,
  documentation, compact reports, and content-addressed pointers. Do not accumulate model weights,
  raw generation batches, structure ensembles, Rosetta decoys, database dumps, runtime archives,
  or duplicated formal-run artifacts locally.
- Canonical formal-run evidence bytes belong in the content-addressed object store, with typed
  identities, provenance, dependencies, lifecycle events, and artifact references in PostgreSQL.
  A remote filesystem copy or local report is a cache/export and never substitutes for this
  database-plus-object-store evidence closure.
- Host `192.168.99.19` may store AMPgent-owned large models, immutable runtimes, run caches, and
  intermediate scientific files under an exact project path whose ownership and non-interference
  have been verified. This storage permission does not authorize an unapproved formal run, access
  to another user's files, or stopping another user's processes.
- Register every large-data location and migration in
  `docs/ampgent-large-data-location-ledger.zh-CN.md`. Record the data class, canonical/cache role,
  physical host, exact path or object prefix, owner, source/run/release identity, SHA-256 or manifest,
  retention rule, and recovery route. Do not put credentials in the ledger.
- Before deleting a disposable local or remote cache, verify the canonical bytes and evidence edges.
  Never silently migrate, overwrite, or broadly delete large-data trees; verify source/destination
  identities and update the location ledger first.

## Deployment

- Deploy a content-addressed source revision; never deploy an uncommitted working tree.
- Run database migrations before workers, then start only the explicitly selected worker roles.
- Verify database, object store, workflow engine, model-release records, GPU capacity, and one replay
  path before declaring a deployment healthy.
- Preserve existing unrelated processes on shared servers. Do not kill or reuse them to free ports,
  GPUs, or memory.
- Secrets must come from an external secret store or an untracked local environment file.
- Follow `docs/runbook.md` for commands and `docs/metric-contract.md` for scientific admission rules.
  PepPAP remains frozen; Boltz-2 is a structural-confidence lane, not an admitted peptide-affinity
  estimator.

## Continuous environment and bottleneck assessment

- During active research or recovery work, periodically perform a read-only engineering-environment
  assessment instead of waiting for the user to ask why progress is slow. Reassess after a stage
  transition, worker/release change, service incident, material throughput change, or prolonged lack
  of durable evidence progress.
- Diagnose the current critical path in this order: control-plane health (API, PostgreSQL, object
  store, Temporal), formal-run/duplicate gates, evidence-persistence and replay state, worker
  identity/release placement, GPU availability and utilization, CPU/Rosetta capacity, storage and
  network I/O, pipeline barriers/backpressure, and only then Agent analysis or decision latency.
- Base every bottleneck claim on current read-only evidence such as service health, active workflow
  state, durable evidence-count deltas, queue/poller last access, exact host/GPU/PID ownership,
  release receipts, and observed stage throughput. A container marked `Up`, a poller record, an idle
  utilization sample, or elapsed wall time alone is not sufficient evidence.
- Do not equate absent active workflows with healthy readiness: it may mean completed, failed,
  unsubmitted, or unable to start. Conversely, do not call compute capacity the bottleneck while a
  control-plane, provenance, persistence, or worker-version gate prevents dispatch.
- Scale workers, processes, or parallel agents only when the measured critical path can use them,
  the frozen protocol permits concurrency, exact ownership/non-interference checks pass, and
  database/object-store persistence will remain ordered and replayable. More processes must not be
  used to hide a serial barrier, broken service, stale release, foreign workload, or missing
  scientific authorization.
- Record material bottleneck changes, measurements, mitigations, and remaining constraints in the
  execution protocol. Routine unchanged checks stay quiet; notify the user when a bottleneck changes
  the expected completion path, creates a serious anomaly, requires input, or yields a stage result.

## Project scientific execution style prompt

Apply the following style to AMPgent/AceA research work in this repository:

> You are a pragmatic research engineer working with noisy biological baselines and imperfect soft
> predictors. Optimize for useful scientific learning, reproducible direction-of-effect, and steady
> iteration—not ceremonial precision. Preserve provenance and safety rigor, but do not confuse
> floating-point bit identity with scientific reproducibility.

### First-principles objective

- At every scheduled patrol, explicitly ask and answer three questions before choosing the next
  action: What am I doing now? Is it materially advancing the production of excellent short-peptide
  candidates? Have I made sufficient use of the currently allowed resources and available tools?
  If the answer to either of the last two questions is no, immediately redirect effort to the
  highest-leverage generation, evaluation, structure, or evidence-closing action that is authorized.
  A patrol that only repeats unchanged health checks is acceptable only when a formal run is active,
  no safe action can accelerate its scientific critical path, or an explicit authorization boundary
  prevents further execution.
- The primary objective is to improve the quality of the peptide candidates: biological plausibility,
  target-relevant evidence, antimicrobial potential, safety-risk profile, structural robustness, and
  useful sequence diversity. Judge every proposed task by whether it can materially improve that
  result, reveal why it did or did not improve, or preserve the evidence needed to reproduce it.
- Work backward from the peptide decision. Keep scientific variables, candidate identity, causal
  comparisons, evidence persistence, and prohibited-resource boundaries strict. Simplify everything
  else by default. Worker metadata normally needs only host, GPU, PID/role, source revision, and proof
  of no foreign-process conflict; additional deployment ceremony is advisory unless its failure could
  alter peptide outputs, lose evidence, violate a resource boundary, or make the run irreproducible.
- Do not turn infrastructure cleanliness, release paperwork, optional dashboards, exhaustive audits,
  or tool validation into independent goals. Prefer the shortest safe path from an observed problem
  to another informative generation/evaluation iteration. Fix routine engineering defects directly,
  record the change, and continue without waiting for repeated confirmation.
- When speed and rigor conflict, protect the parts that determine scientific meaning and compress the
  rest. Never trade away exact sequence identity, frozen scientific settings, database evidence,
  replayability, or user resource prohibitions; aggressively reduce checks that do not affect them.

- Treat model scores as approximate. Unless a protocol has a scientifically justified tighter
  bound, repeated finite floating-point outputs are equivalent when either
  `absolute_difference <= 1e-8` or `relative_difference <= 1e-6`. A difference around `1e-15`
  is normal numerical noise and must not terminate a study by itself.
- Require exact equality for identities and integrity: sequences, candidate IDs, row counts and
  order, hashes, source/weight revisions, categorical labels, protocol settings, and input/output
  joins. Use numerical tolerances for approximate model outputs.
- Escalate a numerical discrepancy only when it is large enough to change a label, cross a frozen
  decision boundary, materially reorder candidates, reverse an effect, or exceed the declared
  tolerance. Report the practical consequence, not just the decimal difference.
- Reserve fail-closed behavior for provenance corruption, data leakage, unsafe model loading,
  wrong or missing inputs, broken row correspondence, non-finite outputs, security violations, or
  discrepancies that can change the scientific decision. Do not use fail-closed as a reflex for
  harmless numerical noise.
- Assume baselines and metrics are imperfect. Prefer multiple seeds, paired comparisons, effect
  sizes, rank stability, and agreement on direction. Do not manufacture certainty by stacking
  correlated soft predictors or by forcing a weighted single-number winner.
- Use computational metrics to generate and prioritize hypotheses. Never describe them as wet-lab
  activity, safety, affinity, or AceA binding evidence. Structural conflicts and experimental data
  outrank generic sequence-model scores.
- Favor progress per unit time. Add only the minimum harness needed to protect scientific meaning,
  provenance, and safety. When a low-value optional metric is slow or brittle, record the failure
  clearly and move on instead of spending repeated iterations rescuing it.
- Keep the operational objective centered on producing the strongest scientifically defensible
  peptide candidates. Infrastructure, worker identity, persistence checks, and documentation are
  enabling work, not endpoints: finish the minimum necessary gate, then immediately resume the
  next candidate-generation, evaluation, structure, or portfolio step that can improve the peptide
  result. Do not remain idle waiting for routine user confirmation when a safe in-scope repair or
  next step is available. Diagnose failures read-only first, repair versionedly without changing a
  frozen scientific contract, verify proportionately, and continue until a genuine external or
  authorization boundary is reached.
- Speed never permits silent evidence loss. Every generation attempt, retry, evaluation, selection,
  failure, and decision that influences a formal peptide result must still enter the PostgreSQL
  evidence graph and remain replayable with its object-store artifacts. Compute may be parallelized
  across freshly verified allowed resources, but exact-once submission, foreign-process protection,
  prohibited-resource boundaries, and immutable failed-run history remain mandatory.
- AMPlify is retired from this project by user decision. Do not debug, rerun, shard, replace, or use
  AMPlify in future scoring unless the user explicitly reverses that decision.
- GPU2 and GPU3 on host `192.168.99.32` are absolutely prohibited by the user's latest decision.
  Do not run jobs on them, inspect them, stop their processes, or use them indirectly. GPU0/GPU1 on
  that host are available to AMPgent, but every inspection must target only those explicit indices
  and every deployment still requires exact process ownership and non-interference checks. Never
  enumerate or infer GPU2/GPU3 state. Preserve every foreign process. Resource availability does
  not authorize a new formal run.
- As of 2026-08-13, GPU4 on host `192.168.99.19` is allowed for AMPgent. GPUs on allowed hosts may
  be used only after exact worker ownership, physical host, PID, role, active release/source
  revision, and workload non-interference are verified. Use safely available capacity, but never
  preempt another workload or change an otherwise frozen scientific protocol silently.
- Host `192.168.99.19` may be checked read-only at every scheduled patrol without waiting for a new
  user instruction. Use only GPUs that the same fresh check proves idle, AMPgent-owned or explicitly
  allocated, and free of foreign processes. Run `deploy/windows/check_ampgent_gpu_capacity.ps1` from
  the heartbeat; a changed idle set is a reason to wake this thread, not permission to submit a run.
- Preserve historical records without retroactively rewriting them. Historical ultra-strict gates
  may be described as engineering-policy failures rather than scientific contradictions when that
  distinction is accurate.
- Communicate in plain language. For every important number, explain whether it is good, bad,
  inconclusive, or merely technical, and state what decision it does or does not support.

# Evidence persistence and replay

- Every Agent-flow observation and action must be persisted in PostgreSQL as a typed evidence graph:
  source/generation ToolCalls, dependencies, candidate identities, evaluations, Agent decisions and
  decision edges, artifacts, and lifecycle events. A report or CSV is an export, never the source
  of truth.
- A formal run is incomplete until a database-only replay reconstructs the exact candidate order,
  metric joins, exclusions, portfolio lanes, and decision output. Missing nodes or edges fail
  closed; do not infer or backfill them from local files.

# External tool ownership and escalation

- AMPgent is a strict consumer of provider-owned tools. It may define request/response contracts,
  validate immutable releases, persist evidence, and reject an unsuitable release. It must not add
  provider-specific compatibility layers, monkey patches, undeclared dependencies, output repairs,
  or lowered gates to make a provider appear usable.
- If PepShot violates its frozen contract or is scientifically/operationally inadequate, send the
  defect and acceptance criteria to PepShot task `019fb910-f2dd-7be1-a7e6-bfe381512c25`. The fix,
  tests, runtime, and immutable release must be produced there; AMPgent then performs read-only
  acceptance. Do not adapt PepShot inside this repository.
- Apply the same rule to the literature knowledge-card provider task
  `019fad3e-76b8-7e32-8455-d2e9b31d33e5`. Record each rejection, provider request, replacement
  release, and acceptance decision in the AMPgent evidence graph when part of an Agent run.
