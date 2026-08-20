from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from pepagent.provenance.hashing import sha256_json

MODEL_ASSAY_REGISTRY_SCHEMA = "ampgent.model-assay-registry.1"
ELIGIBLE_STATUS = "formal_eligible"
KNOWN_STATUSES = {
    "formal_eligible",
    "incumbent_not_enterprise_eligible",
    "available_unvalidated",
    "shadow",
    "blocked",
    "retired",
}


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def _sha256(value: object, *, label: str) -> str:
    text = _text(value, label=label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return text


@dataclass(frozen=True)
class ModelRegistryAudit:
    schema_version: str
    registry_sha256: str
    formal_science_run_authorized: bool
    eligible_models_by_domain: dict[str, tuple[str, ...]]
    eligible_independence_groups_by_domain: dict[str, tuple[str, ...]]
    gaps: tuple[str, ...]
    rejected_models: dict[str, tuple[str, ...]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _formal_eligibility_failures(
    model: Mapping[str, Any], *, calibration_and_ood_required: bool
) -> list[str]:
    failures: list[str] = []
    for field in ("endpoint_semantics", "independence_group", "training_domain"):
        try:
            _text(model.get(field), label=field)
        except ValueError:
            failures.append(f"missing_{field}")
    try:
        _sha256(model.get("runtime_manifest_sha256"), label="runtime_manifest_sha256")
    except ValueError:
        failures.append("missing_runtime_manifest_sha256")

    license_record = model.get("license")
    if not isinstance(license_record, Mapping):
        failures.append("missing_license_record")
    else:
        if license_record.get("commercial_use_allowed") is not True:
            failures.append("commercial_use_not_confirmed")
        try:
            _text(license_record.get("license_id"), label="license_id")
        except ValueError:
            failures.append("missing_license_id")

    validation = model.get("validation")
    if not isinstance(validation, Mapping):
        failures.append("missing_validation_record")
        return failures
    if validation.get("independent_validation_status") != "passed":
        failures.append("independent_validation_not_passed")
    try:
        _sha256(
            validation.get("independent_validation_artifact_sha256"),
            label="independent_validation_artifact_sha256",
        )
    except ValueError:
        failures.append("missing_independent_validation_artifact_sha256")

    if calibration_and_ood_required:
        for evidence_kind in ("calibration", "ood"):
            evidence = validation.get(evidence_kind)
            if not isinstance(evidence, Mapping) or evidence.get("status") != "passed":
                failures.append(f"{evidence_kind}_not_passed")
                continue
            try:
                _sha256(evidence.get("artifact_sha256"), label=f"{evidence_kind}_artifact_sha256")
            except ValueError:
                failures.append(f"missing_{evidence_kind}_artifact_sha256")
    return failures


def audit_model_assay_registry(
    *, registry: Mapping[str, Any], enterprise_contract: Mapping[str, Any]
) -> ModelRegistryAudit:
    """Fail closed when the scoring panel cannot support an enterprise formal run.

    A model counts only when its deployed runtime, endpoint semantics, commercial-use license,
    independent validation, and (where required) calibration/OOD evidence are all pinned. Two
    models from the same independence group count as one source of evidence.
    """

    if registry.get("schema_version") != MODEL_ASSAY_REGISTRY_SCHEMA:
        raise ValueError("model/assay registry schema is invalid")
    models = registry.get("models")
    if not isinstance(models, list):
        raise ValueError("model/assay registry models must be a list")
    domains = enterprise_contract.get("evidence_domains")
    if not isinstance(domains, Mapping) or not domains:
        raise ValueError("enterprise contract evidence_domains must be a non-empty object")

    eligible_by_domain: dict[str, list[str]] = {str(domain): [] for domain in domains}
    groups_by_domain: dict[str, set[str]] = {str(domain): set() for domain in domains}
    rejected: dict[str, tuple[str, ...]] = {}
    identities: set[tuple[str, str]] = set()

    for index, raw_model in enumerate(models):
        if not isinstance(raw_model, Mapping):
            raise ValueError(f"model registry entry {index} must be an object")
        model_id = _text(raw_model.get("model_id"), label="model_id")
        version = _text(raw_model.get("version"), label=f"version for {model_id}")
        identity = (model_id, version)
        if identity in identities:
            raise ValueError(f"duplicate model registry identity: {model_id}@{version}")
        identities.add(identity)
        domain = _text(raw_model.get("evidence_domain"), label=f"evidence_domain for {model_id}")
        if domain not in domains:
            raise ValueError(f"unknown evidence domain for {model_id}: {domain}")
        status = _text(raw_model.get("status"), label=f"status for {model_id}")
        if status not in KNOWN_STATUSES:
            raise ValueError(f"unknown registry status for {model_id}: {status}")
        display_identity = f"{model_id}@{version}"

        if status != ELIGIBLE_STATUS:
            blockers = raw_model.get("blockers")
            if not isinstance(blockers, list) or not blockers:
                raise ValueError(f"non-eligible model {display_identity} must declare blockers")
            rejected[display_identity] = tuple(
                _text(blocker, label=f"blocker for {display_identity}") for blocker in blockers
            )
            continue

        domain_contract = domains[domain]
        if not isinstance(domain_contract, Mapping):
            raise ValueError(f"enterprise domain contract must be an object: {domain}")
        failures = _formal_eligibility_failures(
            raw_model,
            calibration_and_ood_required=bool(
                domain_contract.get("calibration_and_ood_required", False)
            ),
        )
        if failures:
            rejected[display_identity] = tuple(failures)
            continue
        independence_group = str(raw_model["independence_group"])
        eligible_by_domain[domain].append(display_identity)
        groups_by_domain[domain].add(independence_group)

    gaps: list[str] = []
    for domain, raw_domain_contract in domains.items():
        if not isinstance(raw_domain_contract, Mapping):
            raise ValueError(f"enterprise domain contract must be an object: {domain}")
        minimum = int(raw_domain_contract.get("minimum_independent_models", 0))
        observed = len(groups_by_domain[str(domain)])
        if observed < minimum:
            gaps.append(f"{domain}:independent_models={observed},required={minimum}")

    return ModelRegistryAudit(
        schema_version="ampgent.model-assay-registry-audit.1",
        registry_sha256=sha256_json(registry),
        formal_science_run_authorized=not gaps,
        eligible_models_by_domain={
            domain: tuple(sorted(models_for_domain))
            for domain, models_for_domain in eligible_by_domain.items()
        },
        eligible_independence_groups_by_domain={
            domain: tuple(sorted(groups)) for domain, groups in groups_by_domain.items()
        },
        gaps=tuple(gaps),
        rejected_models=dict(sorted(rejected.items())),
    )


def require_formal_model_registry_audit(audit: Mapping[str, Any]) -> str:
    """Validate the compact audit at the final submission boundary."""

    if audit.get("schema_version") != "ampgent.model-assay-registry-audit.1":
        raise ValueError("model/assay registry audit schema is invalid")
    registry_sha256 = _sha256(audit.get("registry_sha256"), label="registry_sha256")
    gaps = audit.get("gaps")
    if audit.get("formal_science_run_authorized") is not True or gaps not in ([], ()):
        raise ValueError("model/assay registry has unresolved enterprise evidence gaps")
    return registry_sha256
