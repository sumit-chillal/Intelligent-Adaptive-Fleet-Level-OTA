"""
Tests for firmware confidentiality.

The claim being tested: an attacker who can read every byte crossing the broker
learns nothing about the firmware image. That is a different claim from
authenticity — signing already stops them CHANGING the image; this stops them
READING it — and it needs its own negative tests.
"""

from __future__ import annotations

import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.crypto import (
    ManifestRejected,
    decrypt_chunk,
    encrypt_chunk,
    generate_device_keypair,
    new_content_key,
    sign_manifest,
    unwrap_content_key,
    verify_manifest,
    wrap_content_key,
)
from app.core.firmware import build_manifest, package_firmware

SERVER_KEY = Ed25519PrivateKey.generate()
SERVER_PUB = SERVER_KEY.public_key()
IMAGE = b"FIRMWARE-SECRET-PAYLOAD-" * 512


def offer(device_priv: bytes, device_pub: bytes, device_id="tcu_D_001"):
    pkg = package_firmware(IMAGE, firmware_id="fw_e", version="1.0.0",
                           model="tcu-sim-v1", chunk_size=1024)
    key = new_content_key()
    wrap = wrap_content_key(key, device_pub, device_id=device_id,
                            campaign_id="c_e")
    manifest = build_manifest(pkg, device_id=device_id, campaign_id="c_e",
                              min_battery=30, min_network_quality=2,
                              key_wrap=wrap)
    return pkg, key, manifest, sign_manifest(manifest, SERVER_KEY).to_wire()


# ------------------------------------------------------------- happy path ---
def test_device_recovers_the_key_and_decrypts():
    priv, pub = generate_device_keypair()
    pkg, key, manifest, wire = offer(priv, pub)

    verified = verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_D_001")
    recovered = unwrap_content_key(verified, priv, device_id="tcu_D_001")
    assert recovered == key

    payload = pkg.chunk_payload(0, "c_e", recovered)
    import base64
    plaintext = decrypt_chunk(recovered, 0,
                              base64.b64decode(payload["data"]), pkg.firmware_id)
    assert plaintext == pkg.chunks[0]
    assert hashlib.sha256(plaintext).hexdigest() == pkg.chunk_hashes[0]


# ------------------------------------- ATTACK: the broker reads everything ---
def test_ciphertext_reveals_nothing_to_an_observer():
    """The whole point. The broker sees the manifest AND the chunks and still
    cannot recover the firmware, because the ECDH private half never leaves the
    device."""
    _, pub = generate_device_keypair()
    pkg, key, _manifest, _wire = offer(b"\x00" * 32, pub)

    ciphertext = encrypt_chunk(key, 0, pkg.chunks[0], pkg.firmware_id)
    assert pkg.chunks[0] not in ciphertext
    assert b"FIRMWARE-SECRET-PAYLOAD" not in ciphertext

    # An attacker with a DIFFERENT device key cannot unwrap.
    other_priv, _ = generate_device_keypair()
    _, _, manifest, _ = offer(*generate_device_keypair())
    with pytest.raises(ManifestRejected):
        unwrap_content_key(manifest, other_priv, device_id="tcu_D_001")


def test_wrong_device_cannot_unwrap():
    """Each wrap is bound to one device's public key AND its id."""
    priv_a, pub_a = generate_device_keypair()
    priv_b, _pub_b = generate_device_keypair()
    _, _, manifest, _ = offer(priv_a, pub_a)
    with pytest.raises(ManifestRejected):
        unwrap_content_key(manifest, priv_b, device_id="tcu_D_001")


def test_key_wrap_is_bound_to_the_device_id():
    """Even the right private key fails under the wrong identity, because the
    id is mixed into the HKDF derivation."""
    priv, pub = generate_device_keypair()
    _, _, manifest, _ = offer(priv, pub, device_id="tcu_D_001")
    with pytest.raises(ManifestRejected):
        unwrap_content_key(manifest, priv, device_id="tcu_D_009")


# ------------------------------------ ATTACK: tamper with the ciphertext ----
def test_flipped_ciphertext_bit_is_rejected():
    """GCM authenticates as well as encrypts, so corruption is caught at
    decryption rather than surviving to the hash check."""
    key = new_content_key()
    ct = bytearray(encrypt_chunk(key, 3, b"payload" * 100, "fw_e"))
    ct[5] ^= 0x01
    with pytest.raises(ManifestRejected) as exc:
        decrypt_chunk(key, 3, bytes(ct), "fw_e")
    assert exc.value.reason == "FAILED_CHUNK_HASH_MISMATCH"


def test_chunk_cannot_be_replayed_at_another_index():
    """The nonce is derived from the index, so a chunk decrypts only in the
    position it was encrypted for. Reordering an image is not possible."""
    key = new_content_key()
    ct = encrypt_chunk(key, 3, b"payload" * 100, "fw_e")
    with pytest.raises(ManifestRejected):
        decrypt_chunk(key, 4, ct, "fw_e")


def test_chunk_cannot_be_moved_between_firmware_versions():
    """firmware_id is associated data, so a chunk is bound to its image."""
    key = new_content_key()
    ct = encrypt_chunk(key, 0, b"payload" * 100, "fw_one")
    with pytest.raises(ManifestRejected):
        decrypt_chunk(key, 0, ct, "fw_two")


# --------------------------------- ATTACK: substitute the key material ------
def test_attacker_cannot_swap_in_their_own_wrapped_key():
    """Key material lives inside the SIGNED manifest.

    Without that, an attacker could replace the ephemeral key and wrapped key
    with their own, and the device would happily decrypt chunks the attacker
    supplied — authenticity of the image would be intact while its CONTENT was
    entirely attacker-chosen.
    """
    import base64
    import json

    priv, pub = generate_device_keypair()
    _, _, _, wire = offer(priv, pub)

    evil_priv, evil_pub = generate_device_keypair()
    evil = wrap_content_key(new_content_key(), evil_pub,
                            device_id="tcu_D_001", campaign_id="c_e")

    manifest = json.loads(base64.b64decode(wire["manifest_b64"]))
    manifest.update(evil)
    wire["manifest_b64"] = base64.b64encode(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).decode()

    with pytest.raises(ManifestRejected) as exc:
        verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_D_001")
    assert exc.value.reason == "FAILED_SIGNATURE_INVALID"


# ------------------------------------------------- nonce discipline ---------
def test_every_chunk_gets_a_distinct_nonce():
    """GCM fails catastrophically on (key, nonce) reuse — it leaks the XOR of
    the plaintexts and the authentication key. Index-derived nonces make reuse
    impossible by construction for a key that is fresh per campaign."""
    from app.core.crypto import chunk_nonce

    nonces = {chunk_nonce(i) for i in range(2000)}
    assert len(nonces) == 2000


def test_encryption_is_optional_and_off_by_default():
    """A device fleet that predates key publication must keep working."""
    pkg = package_firmware(IMAGE, firmware_id="fw_p", version="1.0.0",
                           model="tcu-sim-v1", chunk_size=1024)
    payload = pkg.chunk_payload(0, "c_p", None)
    assert payload["encrypted"] is False

    manifest = build_manifest(pkg, device_id="d", campaign_id="c",
                              min_battery=30, min_network_quality=2)
    assert manifest["enc_alg"] == "none"