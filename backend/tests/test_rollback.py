"""
Tests for rollback.

A recovery path is the one path you cannot debug in production, because by the
time it runs something has already gone wrong. These assert the two rules that
would silently break it: which devices a rollback touches, and how its outcomes
are counted.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.constants import ReasonCode, counts_as_failure, counts_as_success
from app.core.crypto import ManifestRejected, sign_manifest, verify_manifest
from app.core.eligibility import DeviceSnapshot, EligibilityPolicy, evaluate
from app.core.firmware import build_manifest, package_firmware, version_to_code

SERVER_KEY = Ed25519PrivateKey.generate()
SERVER_PUB = SERVER_KEY.public_key()
IMAGE = bytes(range(256)) * 40


def dev(version: str, **kw) -> DeviceSnapshot:
    base = dict(device_id="tcu_D_001", online=True, battery=85,
                network_quality=5, current_version_code=version_to_code(version),
                model="tcu-sim-v1")
    base.update(kw)
    return DeviceSnapshot(**base)


ROLLBACK_TO_1_4 = EligibilityPolicy(
    min_battery=30, min_network_quality=2,
    target_version_code=version_to_code("1.4.0"), is_rollback=True)


# ----------------------------------------------------- who gets rolled back --
def test_device_on_the_bad_version_is_rolled_back():
    """The whole point: a device ahead of the target comes back to it."""
    assert evaluate(dev("1.5.0"), ROLLBACK_TO_1_4).eligible


def test_device_already_on_the_target_is_skipped():
    result = evaluate(dev("1.4.0"), ROLLBACK_TO_1_4)
    assert result.reason is ReasonCode.SKIPPED_ALREADY_ON_TARGET


def test_device_BELOW_the_target_is_left_alone():
    """The subtle rule.

    A device on 1.3.0 never received the bad 1.5.0 release, so the incident
    never reached it. Pushing 1.4.0 onto it would turn a recovery into a second
    rollout — new firmware onto a machine that was fine — and every device
    updated during an incident is a device that can fail during an incident.
    """
    result = evaluate(dev("1.3.0"), ROLLBACK_TO_1_4)
    assert not result.eligible
    assert result.reason is ReasonCode.SKIPPED_ALREADY_ON_TARGET
    assert "never received" in result.detail


def test_forward_campaign_still_behaves_normally():
    """The inverted check must not leak into normal rollouts."""
    forward = EligibilityPolicy(min_battery=30, min_network_quality=2,
                                target_version_code=version_to_code("1.4.0"))
    assert evaluate(dev("1.3.0"), forward).eligible
    assert evaluate(dev("1.4.0"), forward).reason is \
        ReasonCode.SKIPPED_ALREADY_ON_TARGET
    assert evaluate(dev("1.5.0"), forward).reason is \
        ReasonCode.SKIPPED_ALREADY_ON_TARGET


def test_rollback_still_respects_the_health_gate():
    """A rollback is urgent, but flashing a device at 8% battery still bricks
    it. Urgency does not suspend physics."""
    result = evaluate(dev("1.5.0", battery=8), ROLLBACK_TO_1_4)
    assert result.reason is ReasonCode.SKIPPED_INELIGIBLE_LOW_BATTERY


# --------------------------------------------------- outcome classification --
def test_manual_rollback_is_not_a_failure():
    """A device that rolled back on request did what was asked. Counting it as
    a failure would make the adaptive engine shrink the batch during a
    recovery — slowing down the one rollout that most needs to move."""
    assert not counts_as_failure(ReasonCode.ROLLED_BACK_MANUAL)


def test_automatic_rollback_IS_a_failure():
    """A device that reverted on its own means the new image would not boot.
    That is exactly the signal the adaptive engine exists to react to."""
    assert counts_as_failure(ReasonCode.ROLLED_BACK_AUTOMATIC)


def test_manual_rollback_counts_as_a_successful_attempt():
    """Corrects an earlier assumption in this file.

    The first version asserted that neither rollback counted as a success. That
    was wrong, and the live logs showed why: with ROLLED_BACK_MANUAL excluded,
    a rollback batch reported "no devices attempted; all targets were
    ineligible" for a batch in which thirteen devices had just been updated.

    The engine measures attempts. A rollback IS an attempt, and one that
    achieved its goal — so during a recovery the engine can still see a rising
    failure rate and shrink the batch, which is exactly when you want it
    watching.
    """
    assert counts_as_success(ReasonCode.ROLLED_BACK_MANUAL)
    assert not counts_as_success(ReasonCode.ROLLED_BACK_AUTOMATIC)


def test_rollback_batch_is_visible_to_the_adaptive_engine():
    from app.core.adaptive import BatchOutcome

    batch = BatchOutcome(index=1, outcomes=(ReasonCode.ROLLED_BACK_MANUAL,) * 5)
    assert batch.attempted == 5, "a rollback batch must not read as zero attempts"
    assert batch.failure_rate == 0.0


def test_a_rollback_that_starts_failing_is_still_caught():
    """The property the fix protects: if devices begin failing to roll back,
    the engine sees the rate and reacts."""
    from app.core.adaptive import BatchOutcome

    batch = BatchOutcome(index=2, outcomes=(
        ReasonCode.ROLLED_BACK_MANUAL, ReasonCode.ROLLED_BACK_MANUAL,
        ReasonCode.ROLLED_BACK_MANUAL, ReasonCode.FAILED_POOR_NETWORK,
        ReasonCode.FAILED_TIMEOUT))
    assert batch.attempted == 5
    assert batch.failure_rate == pytest.approx(0.4)


# ------------------------------------------------------- signed authority ----
def make_offer(version: str, rollback: bool):
    pkg = package_firmware(IMAGE, firmware_id="fw_r", version=version,
                           model="tcu-sim-v1")
    manifest = build_manifest(pkg, device_id="tcu_D_001", campaign_id="c_r",
                              min_battery=30, min_network_quality=2,
                              rollback=rollback)
    return sign_manifest(manifest, SERVER_KEY).to_wire()


def test_signed_rollback_passes_the_anti_rollback_floor():
    wire = make_offer("1.4.0", rollback=True)
    out = verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_D_001",
                          min_allowed_version_code=version_to_code("1.5.0"))
    assert out["version"] == "1.4.0"


def test_unsigned_downgrade_is_still_refused():
    """Without the flag, the same downgrade is rejected."""
    wire = make_offer("1.4.0", rollback=False)
    with pytest.raises(ManifestRejected) as exc:
        verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_D_001",
                        min_allowed_version_code=version_to_code("1.5.0"))
    assert exc.value.reason == "FAILED_ANTI_ROLLBACK"


def test_attacker_cannot_add_the_rollback_flag():
    """The flag is INSIDE the signature. Flipping it invalidates the manifest,
    so the ability to authorise a downgrade belongs to whoever holds the
    signing key and nobody else."""
    wire = make_offer("1.4.0", rollback=False)
    wire["manifest"]["rollback"] = True
    with pytest.raises(ManifestRejected) as exc:
        verify_manifest(wire, SERVER_PUB, expected_device_id="tcu_D_001",
                        min_allowed_version_code=version_to_code("1.5.0"))
    assert exc.value.reason == "FAILED_SIGNATURE_INVALID"