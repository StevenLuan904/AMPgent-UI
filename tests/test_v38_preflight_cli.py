from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_json
from pepagent.v38_preflight_cli import (
    apply_v38_metric_runtime_overrides,
    require_v38_no_site_metric_bootstrap,
)


def _bundle() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "v37.execution-bundle.1",
        "metric_plugins_by_name": {
            "physicochemical_developability": {"name": "old"},
            "hemolysis_risk": {"name": "old"},
        },
    }
    payload["execution_bundle_identity_sha256"] = sha256_json(payload)
    return payload


def _descriptor(path: Path, name: str) -> None:
    path.write_text(
        json.dumps(
            {
                "name": name,
                "runtime_identity_sha256": "a" * 64,
                "execution_guard": {
                    "contract": {"command_entities": {"adapter_index": 2}},
                    "expectation": {},
                    "paths": {},
                },
            }
        ),
        encoding="utf-8",
    )


def test_metric_runtime_overrides_are_explicit_and_rehash_bundle(tmp_path: Path) -> None:
    physicochemical = tmp_path / "physicochemical.json"
    hemolysis = tmp_path / "hemolysis.json"
    _descriptor(physicochemical, "physicochemical_developability")
    _descriptor(hemolysis, "hemolysis_risk")

    result = apply_v38_metric_runtime_overrides(
        _bundle(),
        [
            f"physicochemical_developability={physicochemical}",
            f"hemolysis_risk={hemolysis}",
        ],
    )

    assert (
        result["metric_plugins_by_name"]["physicochemical_developability"][  # type: ignore[index]
            "runtime_identity_sha256"
        ]
        == "a" * 64
    )
    identity = {
        key: value for key, value in result.items() if key != "execution_bundle_identity_sha256"
    }
    assert result["execution_bundle_identity_sha256"] == sha256_json(identity)
    require_v38_no_site_metric_bootstrap(result)


def test_v38_preflight_rejects_metric_without_no_site_bootstrap() -> None:
    bundle = _bundle()
    plugins = bundle["metric_plugins_by_name"]
    assert isinstance(plugins, dict)
    plugins["physicochemical_developability"] = {
        "execution_guard": {"contract": {"command_entities": {"adapter_index": 1}}}
    }
    plugins["hemolysis_risk"] = {
        "execution_guard": {"contract": {"command_entities": {"adapter_index": 2}}}
    }

    with pytest.raises(ValueError, match="physicochemical_developability.*no-site"):
        require_v38_no_site_metric_bootstrap(bundle)


@pytest.mark.parametrize(
    "override",
    ["missing-separator", "unknown=runtime.json"],
)
def test_metric_runtime_overrides_fail_closed(tmp_path: Path, override: str) -> None:
    if "=" in override:
        _descriptor(tmp_path / "runtime.json", "unknown")
        override = f"unknown={tmp_path / 'runtime.json'}"
    with pytest.raises(ValueError, match="override"):
        apply_v38_metric_runtime_overrides(_bundle(), [override])
