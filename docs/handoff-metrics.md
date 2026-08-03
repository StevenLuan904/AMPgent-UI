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

The corrected ToxinPred3 v0.2 runner also reproduces its pinned model and runtime, but the same
control exposes a scientific conflict: melittin receives ML score `0.820`, then a negative literal
motif lowers the hybrid score to `0.320` and the released threshold labels it `Non-Toxin`. The
validation report therefore separates runtime `complete` from scientific `conflicting`. The result
is retained as negative admission evidence and ToxinPred3 remains soft-only.

LLAMP is reproduced in a separate Python 3.9 CPU environment from source commit `bb48daa`, the
exact peptide-tuned ESM-2 revision `16b0dddc`, the handoff checkpoint, and the locked E. coli genome
features. Every model/config/checkpoint/feature hash matches the handoff. On full-length melittin it
returns `log10(MIC/uM) = 0.60234`, or `4.0026 uM`. This validates finite, sequence-matched inference
only: the public assay organisms and conditions are not the LLAMP E. coli endpoint, and one control
cannot establish calibration. LLAMP therefore remains final-stage soft evidence and never a hard
qualification gate. Its PolyForm Noncommercial license also confines the installed runtime to
noncommercial research unless separately reviewed.

AMPlify 2.0.1 is reproduced in a separate Python 3.6 CPU environment using the exact Bioconda
artifact, entry script, and five balanced-ensemble weight hashes from the handoff. On full-length
melittin it reports AMP probability `0.9995996`; all five submodels independently report values
above `0.998`. This supports the expected generic AMP-positive direction and validates the strict
sequence/ID output contract. It does not calibrate the probability, establish AceA binding, or
justify a hard gate, so `amp_likeness` remains final-stage soft evidence. The runtime keeps the
released `probability > 0.5` label rule and rejects sequences outside its 2-200 canonical-residue
domain as unavailable without affecting sequence eligibility.
