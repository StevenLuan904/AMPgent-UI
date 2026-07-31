# Codex Delivery and Versioning Rules

These rules apply to every Codex change in this repository.

## Git workflow

- `main` is the releasable branch. Do not develop directly on it after the initial repository
  bootstrap.
- Use short-lived branches named `agent/<scope>`; keep each branch focused on one coherent change.
- Commit and push at meaningful checkpoints. Do not leave substantial reproducible work only on one
  machine.
- Before committing, inspect `git status` and the staged diff. Never commit credentials, `.env`,
  machine-specific configuration, model weights, databases, caches, or generated experiment output.
- Use concise commit subjects. Never force-push shared branches or rewrite published history.
- Run `ruff check src tests` and `pytest -q` before merging. Record any unavoidable skipped check.

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

