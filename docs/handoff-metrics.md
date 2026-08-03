# Optional handoff metrics

The 2026-08-04 metric handoff is integrated as a separate, opt-in Temporal activity lane. Existing
experiment specifications have an empty `optional_metrics` list and therefore retain their prior
behavior. A metric-worker failure is recorded as `unavailable` and leaves sequence eligibility
unchanged unless a future experiment explicitly selects `failure_policy: fail_run`.

## Trust policy

- `physicochemical_developability` is a deterministic descriptor lane. It reproduces the pinned
  modlAMP 4.3.2 protocol and records pH, terminal chemistry, hydrophobicity scale, angle, and both
  package/runtime versions. It is descriptive rather than an experimental developability claim.
- HemoPI2/Macrel, corrected ToxinPred3, LLAMP MIC, and AMPlify are capped at `soft`. They may be
  reported or used as explicitly declared low-priority objectives, but cannot become hard
  qualification or diversity gates.
- PeptiVerse half-life and AggrescanAI APR are capped at `shadow` and cannot enter selection.
- MMseqs2 nearest-neighbor identity is a descriptor only after its reference database snapshot is
  content-addressed. It does not establish biological novelty or function.
- ProsperousPlus and all rejected/blocked audit packages are not callable plugins.

The initial deployment keeps every external adapter disabled. Enabling one requires an untracked
runtime registry entry with an immutable source/model identifier, weight hash when applicable,
license review, and a batch command that returns exactly one sequence-matched row per candidate.
The registry and raw output are hashed and persisted. Secrets must never appear in command arrays.

## Experiment configuration

```yaml
optional_metrics:
  - name: physicochemical_developability
    enabled: true
    trust: descriptor
    stages: [research, final]
    failure_policy: record_unavailable
    parameters:
      ph: 7.4
      c_terminal_amidated: false
      hydrophobic_moment_angle: 100
  - name: hemolysis_risk
    enabled: true
    trust: soft
    stages: [final]
    failure_policy: record_unavailable
  - name: serum_half_life
    enabled: true
    trust: shadow
    stages: [final]
    failure_policy: record_unavailable
```

Optional observations are evaluated after the generation's existing selection step. This first
integration therefore cannot silently alter legacy ranking. Later experiments may explicitly add
admitted soft metrics to `metric_policy`; trust-ceiling validation prevents unsupported hard gates.

## Public-complex control

The first end-to-end control is PDB `8AHS`, human Ca2+/calmodulin bound to the 26-residue bee-venom
peptide melittin. It is useful because the deposited heterodimer supplies an experimental
protein-peptide pose while primary literature independently reports melittin's strong hemolysis,
cytotoxicity, and antimicrobial MIC range. Natural melittin is C-terminally amidated, so that
chemistry must be declared in the descriptor configuration. The control tests data flow and whether
metric directions are qualitatively sensible; one peptide cannot calibrate a model or validate use
as a hard decision gate.

The first isolated external replay enables only Macrel 1.6.1 at commit `8c1f732`. Its two ONNX
asset hashes exactly match the handoff manifest. On full-length amidated melittin from the 8AHS
control it reports AMP probability `0.812` and hemolysis probability `0.990` (`high`), matching the
known qualitative direction. This admits Macrel only as soft evidence; it does not validate the
threshold, substitute for an assay, or activate HemoPI2 by implication.
