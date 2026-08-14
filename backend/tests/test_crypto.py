"""
Security tests for firmware provenance.

These are NEGATIVE tests: each one is an attack, and each must be rejected.
A crypto module with only happy-path tests proves nothing -- signature
verification that always returns True passes every positive test ever written.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.crypto import (
    ManifestRejected,
    canonical_bytes,
    sign_manifest,
    verify_manifest,
)
from app.core.firmware import build_manifest, package_firmware, version_to_code

SERVER_KEY = Ed25519PrivateKey.generate()
SERVER_PUB = SERVER_KEY.public_key()
ATTACKER_KEY = Ed25519PrivateKey.generate()

IMAGE = bytes(range(256)) * 80  # 20 KiB of deterministic bytes


def make_offer(device_id="tcu_B_001", campaign_id="c_demo", rollback=False,
               version="1.4.0", key=SERVER_KEY):
    pkg = package_firmware(IMAGE, firmware_id="fw_1", version=version, model="tcu-sim-v1")
    manifest = build_manifest(pkg, device_id=device_id, campaign_id=campaign_id,
                              min_battery=30, min_network_quality=2, rollback=rollback)
    return pkg, manifest, sign_manifest(manifest, key).to_wire()


# ------------------------------------------------------------- happy path ----
def test_genuine_manifest_is_accepted():
    _, manifest, wire = make_offer()
    out = verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_B_001",
                          expected_campaign_id="c_demo",
                          expected_nonce=manifest["nonce"])
    assert out["version"] == "1.4.0"


# --------------------------------------------------- ATTACK 1: forged key ----
def test_manifest_signed_with_an_attacker_key_is_rejected():
    """The core property: an attacker with total network control but no
    signing key cannot get firmware installed."""
    _, _, wire = make_offer(key=ATTACKER_KEY)
    with pytest.raises(ManifestRejected) as exc:
        verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_B_001")
    assert exc.value.reason == "FAILED_SIGNATURE_INVALID"


# ------------------------------------------------ ATTACK 2: tampered body ----
def test_tampering_with_the_image_hash_is_rejected():
    """Swap the payload, keep the genuine signature."""
    _, _, wire = make_offer()
    wire["manifest"]["sha256"] = "0" * 64
    with pytest.raises(ManifestRejected) as exc:
        verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_B_001")
    assert exc.value.reason == "FAILED_SIGNATURE_INVALID"


def test_tampering_with_any_field_is_rejected():
    for field, value in [("version", "9.9.9"), ("size", 1), ("min_battery", 0),
                         ("chunk_count", 1), ("rollback", True)]:
        _, _, wire = make_offer()
        wire["manifest"][field] = value
        with pytest.raises(ManifestRejected):
            verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_B_001")


# ------------------------------------------- ATTACK 3: lateral replay --------
def test_manifest_for_another_device_is_rejected():
    """A genuine, correctly signed offer captured off the wire and replayed at
    a different device. This is why device_id is inside the signature."""
    _, _, wire = make_offer(device_id="tcu_B_001")
    with pytest.raises(ManifestRejected) as exc:
        verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_C_003")
    assert exc.value.reason == "FAILED_SIGNATURE_INVALID"
    assert "addressed to" in exc.value.detail


# ------------------------------------------ ATTACK 4: temporal replay --------
def test_replayed_old_campaign_is_rejected():
    _, manifest, wire = make_offer(campaign_id="c_old")
    with pytest.raises(ManifestRejected):
        verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_B_001",
                        expected_campaign_id="c_current",
                        expected_nonce=manifest["nonce"])


def test_replayed_nonce_is_rejected():
    _, _, wire = make_offer()
    with pytest.raises(ManifestRejected):
        verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_B_001",
                        expected_nonce="a-different-nonce")


# --------------------------------------------- ATTACK 5: downgrade -----------
def test_downgrade_below_the_version_floor_is_rejected():
    """The subtle attack: replay a genuine OLD manifest to push a device back
    to a version with a known vulnerability."""
    _, _, wire = make_offer(version="1.2.0")  # version_code 10200
    with pytest.raises(ManifestRejected) as exc:
        verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_B_001",
                        min_allowed_version_code=version_to_code("1.4.0"))
    assert exc.value.reason == "FAILED_ANTI_ROLLBACK"


def test_authorised_rollback_is_allowed_below_the_floor():
    """An operator-initiated rollback IS legitimate -- but only because the
    rollback flag is inside the signed structure, so only the real server can
    set it."""
    _, _, wire = make_offer(version="1.2.0", rollback=True)
    out = verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_B_001",
                          min_allowed_version_code=version_to_code("1.4.0"))
    assert out["rollback"] is True


# ------------------------------------------------ malformed input ------------
@pytest.mark.parametrize("wire", [
    {}, {"manifest": {}}, {"manifest": {}, "signature": 123},
    {"manifest": {}, "signature": "zz", "sig_alg": "ed25519"},
    {"manifest": {}, "signature": "00", "sig_alg": "rsa"},
])
def test_malformed_envelopes_are_rejected_not_crashed(wire):
    with pytest.raises(ManifestRejected):
        verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_B_001")


# ------------------------------------------------ canonical encoding ---------
def test_canonical_encoding_is_key_order_independent():
    """If this breaks, valid manifests start failing verification and somebody
    'fixes' it by disabling the check."""
    assert canonical_bytes({"b": 2, "a": 1}) == canonical_bytes({"a": 1, "b": 2})


def test_signature_survives_a_json_round_trip():
    import json
    _, manifest, wire = make_offer()
    wire = json.loads(json.dumps(wire))  # exactly what MQTT does to it
    verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_B_001",
                    expected_nonce=manifest["nonce"])


def test_sign_manifest_refuses_to_sign_without_required_bindings():
    for missing in ("device_id", "campaign_id", "nonce", "sha256"):
        _, manifest, _ = make_offer()
        del manifest[missing]
        with pytest.raises(ValueError, match=missing):
            sign_manifest(manifest, SERVER_KEY)
