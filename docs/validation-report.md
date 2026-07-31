# Affinity evaluator validation report

## PepPAP: frozen and rejected from the experiment workflow

The official 1G6R example was reproduced on 2026-07-31:

- published expected pKd/pKi: `4.204`; reproduced: `4.204`
- published expected delta-G: `-5.711 kcal/mol`; reproduced: `-5.711 kcal/mol`
- canonical validation run: `80a4fe74-07d6-4398-975d-dfbb3dce8368`
- source commit: `5255d72873e1bf307b33d356a7ca143cb6102fdc`
- weights revision: `2481e8cdbe9b1e400c0a82c02d5e44488fa5784c`
- five-checkpoint manifest SHA-256:
  `499c1f38745e37bdff0659813def29e010cf7436ee3c5696a818d000c2833468`
- environment SHA-256:
  `5bd7f4ec4dd1dc3e0908aa2a93ce3b9593ccdd6ef7d4b5e7fe048cbb88930a46`

The upstream repository is not runnable unchanged. Compatibility version `compat-v2` applied only:

1. `./units` to the repository's actual `./utils` module path;
2. CPU `map_location` when loading checkpoints saved on CUDA.

The published model definition also assigns `encode_protein_reshape` from `encode_peptide`. This was
not changed because the released weights may have been trained with that behavior. A repaired model
would be a distinct evaluator requiring retraining and calibration.

Final admission verdict (2026-07-31): do not run PepPAP for new work and do not use it for candidate
ranking, optimization or new validation imports. The reproduced values and artifacts remain in the
evidence store solely to preserve the complete decision history. Reproduction of one bundled example
proves software replay, not scientific validity or transfer to the project's target families.

## PPI-Affinity: external evidence only

The publication describes an SVM ensemble based on structural descriptors calculated from a
protein-peptide complex PDB. Therefore it belongs after Boltz-2, not in the sequence-only lane. The
public service was unreachable during validation, and no downloadable versioned model weights or
inference package were found.

Verdict: do not optimize against PPI-Affinity in MVP v1. If the service becomes available, persist
its model/service version, submitted PDB hash and returned raw report as external evidence. Promote
it only if a replayable release is obtained.

## Rosetta FlexPepDock: MVP-v2 formal validation

The admitted implementation uses PyRosetta
`2026.29+releasequarterly.80a0635615`, wheel SHA-256
`25254a10363eb5bdc0e1f3f36cbf846cb513958281041dd2b1b259610de2e733`, `ref2015`,
FlexPepDock prepack/refinement and InterfaceAnalyzer. Per-decoy `dG_separated` comes from the typed
InterfaceAnalyzer getter. The standard FlexPepDock `reweighted_sc` is reconstructed as total score
+ isolated peptide score + cross-interface score and is used to rank decoys. The primary run value
is the median `dG_separated` among the ten best reweighted decoys; the full distribution and minimum
are also retained.

An exact-seed 2DS8 engineering replay passed byte-for-byte after canonicalizing the absolute output
path that Rosetta writes into PDB energy-table footers. Both runs produced the same result JSON,
prepacked PDB and refined PDB. The single-decoy smoke value was `-18.2969842019 REU` with peptide
backbone RMSD `1.0856442212 Å`. This proves deterministic execution, not affinity calibration.

The first complete formal case is 2DS8, run
`aa60bfad-97f4-44b0-a0b1-969d985fe5fe`. It produced 200/200 valid decoys and completed as
`succeeded`. The primary top-ten median was `-32.9330883679 REU`; the all-decoy median and minimum
were `-30.6040018248` and `-33.0551035168 REU`. Peptide backbone RMSD had minimum/median/maximum
`0.3752/0.5629/2.5055 Å`; 73.0% of decoys were within 1 Å and 94.5% within 2 Å. The best
`reweighted_sc` decoy had RMSD `0.4515 Å`, and the top-ten RMSD median was `0.4349 Å`. This is a
positive native-start recovery and ranking check for this public complex, not an experimental
affinity calibration.

The 2DS8 database record contains seven evaluations, the explicit
`rcsb-pdb-retrieval -> refines -> pyrosetta-flexpepdock-interface-analyzer` dependency, and 406
artifact links representing 405 unique content hashes. Its raw output SHA-256 is
`2d771d16a44bae38f0973ae60d9236df656650affedea1fb58832eb17fa6ec89`; its environment SHA-256 is
`61dd0ef4792617951ad1d47040b2178ed1aeb90629dae1ec5b9d224d30421c6a`.

The second complete case is 1NVR, run
`6e5405f4-b1c1-4087-88be-dfdb5e76e346`. It also produced 200/200 valid decoys and completed as
`succeeded`. Primary top-ten median dG was `-26.8628380275 REU`; the all-decoy median and minimum
were `-25.2700130881` and `-28.9568627008 REU`. Peptide backbone RMSD had
minimum/median/maximum `0.3739/0.5477/0.9344 Å`: every decoy remained within 1 Å, the best
`reweighted_sc` decoy was at `0.5665 Å`, and the top-ten RMSD median was `0.5073 Å`. The
reweighted-score/RMSD Spearman coefficient was only `0.3529`, which is interpreted with the narrow
all-near-native RMSD range rather than presented as a broad docking-discrimination claim.

The 1NVR record contains seven evaluations, 406 artifact links (405 unique hashes, 79,072,545
linked bytes), and the explicit retrieval-to-refinement dependency. Its raw output SHA-256 is
`f1ce0aa60e31a623df37e2d6dc4f76372b210431091437264cefaddcd926021e`.

One further durable native-start case is running with 200 independently seeded decoys:

- Rosetta's official 1ER8 integration-test input:
  `5a121752-b844-4278-bfc5-a3149f4d1a1b`.

The raw RCSB 1ER8 coordinate experiment is intentionally retained as failed evidence. Its replacement
run `10bc074c-2126-4218-8ca3-10af3cc8c279` reproducibly reached Rosetta's internal
`PackstatCalculator` pose-size assertion during FlexPepDock prepack. The legacy RCSB coordinates
contain non-standard DHI and additional numbered receptor residues; deleting unsupported records
does not reproduce Rosetta's published 1ER8 benchmark input. The official Rosetta input has its own
source URL and SHA-256
`47de41c87cbf53cb06a67f0ac3e8e834c63f27a0c7b94d8178be36c5ec6bd125`, and passed an isolated
prepack before the replacement formal run was submitted. No historical run or source artifact was
overwritten.

Rosetta energy units are never converted to kcal/mol, Kd or a claim of absolute binding affinity.
The visual snapshot auditor is a separately deployed, non-blocking auxiliary module. The formal
Rosetta workflow never invokes it, waits for it or consumes its output as a decision metric. Final
admission remains pending until the two running cases, artifact uploads and database evidence queries
complete.

## AceA durable MVP run

- experiment run: `126cfb93-d801-4f89-8dac-70aba3fa0ffd`
- Temporal workflow: `pepagent-run-126cfb93-d801-4f89-8dac-70aba3fa0ffd`
- raw/canonical specification SHA-256:
  `2d6c77dc23c486098d9c3f36eb7ad78b93e1e6564ec47d905c0edcd8fff833c4`
- target: UniProtKB/Swiss-Prot `P0A9G6` (E. coli K-12 AceA), sequence SHA-256
  `3a113be0188b92e4e130d3165f4fdb2a3f0df846284e539a8af01c8f038ade10`
- scope: blind single-sequence structure smoke test; no pocket constraint, no MSA server and no
  affinity evaluator

The PepMLM worker was deliberately absent when the run was submitted. Temporal retained the same
scheduled activity. After the worker returned, an upstream PyTorch security gate rejected the old
environment; the environment was upgraded and the same run resumed without resubmission. The
successful call is attempt 3, tool-call ID `d45ff8c1-2f49-412d-a0c4-b29b2df11e57`.

PepMLM generated four 12-residue candidates. The promoted candidate is `KSSVGVVVGNPA`, with
conditional PPL `5.9314780854` and NLL `1.7802734375` nats/residue. These values measure model
compatibility with the target context, not binding affinity or enzyme inhibition.

Admitted model releases:

- PepMLM-650M release `799ddd78-5661-4afd-a3e1-5268b5b861d4`, weights SHA-256
  `8a3225bca1f9acd9f701ca2e46597c12bab92320e32b68f380ddf3b6d3b20770`;
- Boltz-2-structure release `719a6412-4c9a-48a8-b13f-df2a0051b5e7`, structure checkpoint
  SHA-256 `090e82ac8c92f5e943fa1b39e7410a44027bea7243c0bbb3caa67a77fc1428e1` and
  molecular-resource SHA-256 `39e076d96dbec6b4e86982bbda16f3a53a2a60c9bdc17828d88f6f9a0c7d1fd7`.

The same workflow completed successfully after recoverable Boltz activity failures. Attempts 1-3
identified launcher/checkpoint-download problems; attempt 4 exposed Boltz's undeclared optional
cuEquivariance kernel import. The adapter then used Boltz's documented `--no_kernels` fallback as
an explicit parameter. Attempt 5 succeeded without creating a replacement run.

Boltz evidence for `KSSVGVVVGNPA`:

- tool-call ID: `566eb223-7c6e-474f-84df-6e77bb66d7d9`, attempt `5`;
- platform release SHA-256:
  `45264aa5fbb4365befda2d323a964de3f18ec1acd42b9d1699fe581cb3e70988`;
- executed-assets aggregate SHA-256:
  `a9d918128c163b96f0067d4c39f5bbaefa1d64a7d5b29e951182bfd7c3aef653`;
- raw output SHA-256:
  `a5cfcfd21bfe971c13cc55c440202ba8726fb2862f8e3f7ff1057f8c250a66d3`;
- predicted complex CIF SHA-256:
  `57152c5e0d6f65c438e37d2b20a50e36717e1777a0e2f177b652406fc59cf984`;
- confidence `0.4938615`, ipTM `0.3618591`, protein-peptide pair-ipTM `0.1402254`, and
  complex ipLDDT `0.4555072`.

The weight manifest committed with this call contains both the exact structure checkpoint and the
`mols.tar` molecular-resource archive. The environment manifest records Python 3.11.10, Torch
2.6.0+cu124, Boltz 2.2.1, RTX 3090 and the platform release above.

Scientific verdict: the durable system and evidence path pass, but this blind candidate does not.
The low pair-ipTM is weak protein-peptide interface evidence. It must not be promoted as an AceA
inhibitor, binding-affinity hit or Kd claim. Pocket-aware generation/structure work is a later,
separately specified experiment.

Terminal lifecycle event: sequence `5`, `run.succeeded`, with one structure, zero affinity results
and `affinity_lane=not_admitted`. The final run status is `succeeded`; the promoted candidate remains
`structure_scored`, not a biologically validated selection.
