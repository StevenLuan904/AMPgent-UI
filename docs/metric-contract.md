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
- A peptide is encoded as a second protein chain for peptide-protein complex co-structure
  prediction. This is supported by the biomolecular/multichain structure model and must not be
  confused with the separate affinity head.
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
- MVP-v1 execution status: not run. Metric enums and this contract are schema preparation, not
  evidence that Rosetta, FlexPepDock or `dG_separated` was executed.

### Codex snapshot critic (provisional P1 shadow evidence)

- Inputs: a deterministic multi-view render bundle, pocket evidence card, contact map, confidence
  overlays and coordinate-derived audit table for one exact structure artifact.
- Outputs: structured failure flags, cited visual/coordinate evidence, uncertainty and a suggested
  next action. The exact model snapshot, prompt/render hashes, camera parameters, image hashes and
  raw response are mandatory.
- Decision role: none until a versioned protein-peptide validation set demonstrates reproducible
  incremental error detection over coordinate checks alone. It is not an affinity estimator,
  scalar metric or substitute for atomic-coordinate analysis.

## Promotion rule

A candidate can enter the high-precision queue only when it has PepMLM conditional likelihood
evidence and Boltz-2 complex-confidence evidence. No absolute-affinity score participates in MVP
promotion until a versioned evaluator passes replay, domain-validity and calibration gates.

## Metric-role policy (MVP-v2)

Metrics are not collapsed into one weighted score. Every experiment declares a role and the stage
where the rule applies:

- `qualification`: a non-compensatory acceptance interval. Passing by a wider margin does not
  improve rank. Stability/developability and structure gates belong here.
- `objective`: minimized or maximized, in declared priority order, only after hard qualifications
  pass. A validated affinity measurement belongs here; Rosetta dG remains only a same-protocol
  relative proxy.
- `diversity`: a minimum coverage constraint. Diversity is not maximized without bound.
- `diagnostic`: persisted for explanation and future policy changes but never used implicitly.

Missing-data behavior is explicit (`fail`, `worst`, or `ignore`). A high objective value can never
compensate for a failed hard qualification. This is feasibility-first constrained selection, not a
fixed weighted sum.

### Sequence developability versus stability

The deterministic `sequence-developability-audit` records hydrophobic fraction, the longest
contiguous hydrophobic run, and the longest identical-residue run. These are early low-complexity
and aggregation/solubility risk flags, not experimental
claims about solubility, plasma half-life, protease resistance, chemical degradation, or
conformational stability. Those meanings of stability require separate admitted evaluators or
assays with their own provenance.

The AceA v4 policy provisionally requires a maximum hydrophobic run of four and a hydrophobic
fraction no greater than 0.60. These are task-level aqueous-peptide guardrails and must be calibrated
against a future project dataset; they are not universal peptide laws. The sequence
`KSAVVVVVVNGA` has six consecutive valines embedded in a seven-residue hydrophobic run and has a
hydrophobic fraction of 8/12, so it fails the provisional guardrails even if a structure or Rosetta
score looks favorable.

Scientific basis and limitations:

- Systematic AMP studies found a hydrophobicity window: excess hydrophobicity increased
  self-association and hemolysis while reducing antimicrobial activity outside the useful window
  (Chen et al., J Biol Chem 2007, PMID 17158938).
- Charge distribution and hydrophobic residue content jointly changed aggregation and mammalian
  membrane damage in designed peptides (Yin et al., J Biol Chem 2012, PMID 22253439).
- These studies support a warning and qualification policy, but do not validate the exact AceA
  thresholds or turn sequence heuristics into measured stability.
