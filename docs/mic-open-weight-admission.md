# Open-weight MIC model admission

This checkpoint admits MIC predictors only when both executable inference code and exact model
weights are publicly retrievable and locally hash-locked. Predictions are supporting evidence, not
wet-lab MIC measurements or hard sequence gates.

## Admitted models

| Model | Endpoint | Public-weight status | Agent use |
| --- | --- | --- | --- |
| LLAMP | E. coli-conditioned log10 MIC and MIC in µM | Code, base model, checkpoint, and genome features reproduced and hash-locked | Soft evidence, reported independently |
| AMP-READ | Generic mixed-species log10 MIC and MIC in µM | Four released CNN/Transformer/Attention/LSTM checkpoints reproduced and individually hash-locked | Soft cross-model evidence; ensemble and all components retained |
| ToxinPred3 | General peptide toxicity classification | Public model and motifs reproduced and hash-locked | Required soft safety evidence; conflicts are preserved |

No MIC values from different endpoint families are averaged. A target-conditioned result and a
mixed-species result answer different questions, so their disagreement is reported rather than
collapsed into a synthetic consensus.

## Public-control replay

The public PDB 8AHS calmodulin–melittin complex supplies the melittin peptide
`GIGAVLKVLTTGLPALISWIKRKRQQ` as a strong AMP control.

| Model | Melittin result | Interpretation |
| --- | ---: | --- |
| LLAMP | 4.0026 µM (log10 0.6023) | E. coli-conditioned soft estimate |
| AMP-READ ensemble | 11.3321 µM (log10 1.0543) | Generic mixed-species soft estimate |
| AMP-READ CNN / Transformer / Attention / LSTM | log10 1.0687 / 0.9363 / 0.9944 / 1.2179 | Component spread is retained |

The two families differ by 0.452 log10 units, or about 2.83-fold in their displayed µM values. This
is not treated as an error because their training endpoints differ. AMP-READ also assigned a weak
non-AMP control 3176.7 µM, providing the expected potency direction in this limited replay. This
small comparison is not similarity-isolated and does not establish assay calibration.

ToxinPred3 remains mandatory but soft: its corrected melittin replay returned ML 0.82 and hybrid
0.32/Non-Toxin, conflicting with melittin's known toxic/hemolytic direction. The conflict is stored
as negative scientific evidence and prevents hard-gating use; it does not justify deleting the
model.

## Not admitted yet

| Candidate | Reason |
| --- | --- |
| BERT-AmPEP60 | Public checkpoint links returned an HTML access page instead of model bytes during admission |
| esAMPMIC | Public Git LFS weight retrieval did not complete reproducibly during admission |

Both remain candidates. They can be admitted only after exact checkpoint bytes are retrieved,
hashed, replayed on controls, and assigned a clearly labelled biological endpoint.
