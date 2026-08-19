from pathlib import Path

SCRIPT = Path("deploy/remote/attest_v38_boltz_runtime.sh")


def test_attestation_checks_real_cache_bytes_and_guarded_smoke() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "stat -c %s" in text
    assert "sha256sum" in text
    assert "boltz2_conf.ckpt" in text
    assert "mols.tar" in text
    assert "find \"$CACHE_DIR/mols\"" in text
    assert "GUARDED_SMOKE_SHA256" in text
    assert "v38.boltz-runtime-cache-attestation.1" in text


def test_attestation_binds_frozen_authoritative_hashes() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "090e82ac8c92f5e943fa1b39e7410a44027bea7243c0bbb3caa67a77fc1428e1" in text
    assert "39e076d96dbec6b4e86982bbda16f3a53a2a60c9bdc17828d88f6f9a0c7d1fd7" in text
