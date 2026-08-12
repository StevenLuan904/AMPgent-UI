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

## Project scientific execution style prompt

Apply the following style to AMPgent/AceA research work in this repository:

> You are a pragmatic research engineer working with noisy biological baselines and imperfect soft
> predictors. Optimize for useful scientific learning, reproducible direction-of-effect, and steady
> iteration—not ceremonial precision. Preserve provenance and safety rigor, but do not confuse
> floating-point bit identity with scientific reproducibility.

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
- AMPlify is retired from this project by user decision. Do not debug, rerun, shard, replace, or use
  AMPlify in future scoring unless the user explicitly reverses that decision.
- Host `192.168.99.32` is temporarily prohibited by user decision. Do not run jobs, inspect or use
  its GPUs, stop processes, or otherwise touch workloads on that host until the user explicitly
  lifts the restriction. This whole-host prohibition explicitly includes GPU3 and GPU4; it is not
  permission to contact the host or use a different GPU there.
- As of 2026-08-12, GPUs on other hosts may be used for AMPgent work after exact worker ownership,
  physical host, PID, role, active release/source revision, and workload non-interference are
  verified. This resource permission does not authorize an otherwise unapproved scientific run.
- GPU4 on host `192.168.99.19` is explicitly prohibited by the user's latest instruction. Do not
  schedule work on it even if it appears idle.
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
