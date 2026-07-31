from enum import StrEnum


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CandidateStatus(StrEnum):
    GENERATED = "generated"
    PPL_SCORED = "ppl_scored"
    STRUCTURE_QUEUED = "structure_queued"
    STRUCTURE_SCORED = "structure_scored"
    ROSETTA_QUEUED = "rosetta_queued"
    ROSETTA_SCORED = "rosetta_scored"
    SELECTED = "selected"
    MUTATED = "mutated"
    REJECTED = "rejected"
    FAILED = "failed"


class EvaluationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class MetricName(StrEnum):
    CONDITIONAL_NLL = "conditional_nll"
    CONDITIONAL_PPL = "conditional_ppl"
    BOLTZ2_CONFIDENCE = "boltz2_confidence"
    BOLTZ2_IPTM = "boltz2_iptm"
    BOLTZ2_PAIR_IPTM = "boltz2_pair_iptm"
    BOLTZ2_COMPLEX_IPLDDT = "boltz2_complex_iplddt"
    PEPPAP_PKD_PKI = "peppap_pkd_pki"
    PEPPAP_DELTA_G_KCAL_MOL = "peppap_delta_g_kcal_mol"
    PEPPAP_KD_EQUIVALENT_MOLAR = "peppap_kd_equivalent_molar"
    PPI_AFFINITY_PREDICTION = "ppi_affinity_prediction"
    ROSETTA_DG_SEPARATED_REU = "rosetta_dg_separated_reu"
    ROSETTA_DG_MINIMUM_REU = "rosetta_dg_minimum_reu"
    ROSETTA_PEPTIDE_BB_RMSD_ANGSTROM = "rosetta_peptide_bb_rmsd_angstrom"
    ROSETTA_INTERFACE_SCORE = "rosetta_interface_score"
    ROSETTA_REWEIGHTED_SCORE = "rosetta_reweighted_score"
    ROSETTA_INTERFACE_HBONDS = "rosetta_interface_hbonds"
    ROSETTA_BURIED_SURFACE_AREA = "rosetta_buried_surface_area"
