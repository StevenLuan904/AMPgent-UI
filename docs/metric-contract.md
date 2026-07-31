# MVP metric contract

## Binding-affinity admission gate

### PepPAP (frozen; rejected from the workflow)

- Input: target protein sequence and peptide sequence.
- Native output: a mixed `pKd/pKi` prediction and derived binding free energy.
- Stored values: original pK, original ΔG, and `10^-pK M` as an explicitly derived
  *Kd-equivalent* value.
- Decision role: none. It must not be executed, imported as new evidence, used for ranking, or used
  as an optimization objective. The prior official-example reproduction is retained only as an
  immutable negative admission record.
- Reproducibility contract: source commit, all five checkpoint hashes, environment lock,
  normalized inputs, random seed (if applicable), raw stdout, parsed output, and parser version.
- Known implementation caveat: the upstream network implementation assigns
  `encode_protein_reshape` from `encode_peptide`. The unmodified upstream result must be
  reproduced first; any repaired implementation is a separately versioned evaluator and must
  never overwrite upstream evidence.

### PPI-Affinity (cross-check)

- Decision role: independent supporting evidence when a callable, versioned implementation can
  be reproduced.
- Acceptance gate: downloadable code and weights, or an API that exposes a stable model version
  and retains enough input/output evidence for replay.
- If the public web service is the only runnable form, results are stored as external evidence
  and cannot be an automatic optimization objective.

### Boltz-2 (structure only)

- Outputs: complex structure, confidence score, ipTM, pairwise ipTM, and complex ipLDDT.
- The affinity head is not called for a peptide represented as a protein chain. Boltz-2 affinity
  is documented for a single small-molecule ligand and is outside this peptide use case.
- Pinned Boltz 2.2.1 downloads its affinity checkpoint unconditionally. The project adapter creates
  an explicit zero-byte disabled sentinel and rejects any non-empty affinity checkpoint, preventing
  both the unnecessary download and accidental affinity-head execution.
- The RTX 3090 worker uses Boltz's official `--no_kernels` path. This avoids an undeclared optional
  cuEquivariance runtime and makes the slower PyTorch fallback an explicit, persisted run parameter.

### Rosetta FlexPepDock (optional P1 structural rescorer)

- Inputs: only the highest-confidence Boltz-2 complex poses.
- Stored outputs: `dG_separated` in Rosetta energy units (REU), `I_sc`, `reweighted_sc`, interface
  hydrogen bonds, buried surface area, the refined pose, Rosetta commit/release and flags.
- Decision role: relative ranking within the same target and protocol. Rosetta energy units are
  not converted into Kd and are never labelled as experimental binding free energy.

## Promotion rule

A candidate can enter the high-precision queue only when it has PepMLM conditional likelihood
evidence and Boltz-2 complex-confidence evidence. No absolute-affinity score participates in MVP
promotion until a versioned evaluator passes replay, domain-validity and calibration gates.
