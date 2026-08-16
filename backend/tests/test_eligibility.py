"""
Tests for the eligibility gate.

The demo tests at the bottom assert the exact behaviour for tcu_D_004 and
tcu_D_005, so the staged failures are a proven property rather than a hope.
"""

from __future__ import annotations

import pytest

from app.constants import ReasonCode, counts_as_failure
from app.core.eligibility import DeviceSnapshot, EligibilityPolicy, evaluate

POLICY = EligibilityPolicy(min_battery=30, min_network_quality=2,
                           target_version_code=10400, offline_ttl_seconds=20)


def dev(**kw) -> DeviceSnapshot:
    base = dict(device_id="tcu_X_001", online=True, battery=85,
                network_quality=5, current_version_code=10300, model="tcu-sim-v1")
    base.update(kw)
    return DeviceSnapshot(**base)


def test_healthy_device_is_eligible():
    assert evaluate(dev(), POLICY).eligible


def test_low_battery_is_skipped_not_failed():
    """The single most important property in this module.

    A skip must never feed the failure rate, or the adaptive engine shrinks the
    batch as punishment for the system's own correct safety decision.
    """
    result = evaluate(dev(battery=8), POLICY)
    assert not result.eligible
    assert result.reason is ReasonCode.SKIPPED_INELIGIBLE_LOW_BATTERY
    assert not counts_as_failure(result.reason)
    assert "8%" in result.detail and "30%" in result.detail


def test_poor_network_is_skipped():
    result = evaluate(dev(network_quality=1), POLICY)
    assert result.reason is ReasonCode.SKIPPED_INELIGIBLE_POOR_NETWORK
    assert not counts_as_failure(result.reason)


def test_offline_device_is_skipped():
    assert evaluate(dev(online=False), POLICY).reason is ReasonCode.SKIPPED_OFFLINE


def test_stale_heartbeat_is_treated_as_offline():
    """Reported online, but silent for longer than the heartbeat window.

    A device whose process is wedged still holds its broker connection, so the
    last will never fires. The heartbeat window is the backstop.
    """
    result = evaluate(dev(seconds_since_last_seen=45), POLICY)
    assert result.reason is ReasonCode.SKIPPED_OFFLINE


def test_device_already_on_target_is_skipped():
    result = evaluate(dev(current_version_code=10400), POLICY)
    assert result.reason is ReasonCode.SKIPPED_ALREADY_ON_TARGET


def test_already_on_target_wins_over_low_battery():
    """Order matters: a device that needs nothing is fine whatever its battery.

    Reporting LOW_BATTERY for a device that is already up to date would send an
    operator chasing a power problem that has no bearing on the rollout.
    """
    result = evaluate(dev(current_version_code=10400, battery=5), POLICY)
    assert result.reason is ReasonCode.SKIPPED_ALREADY_ON_TARGET


def test_missing_readings_are_refused_not_assumed_safe():
    """Absence of evidence is not evidence of safety."""
    assert evaluate(dev(battery=None), POLICY).reason is \
        ReasonCode.SKIPPED_INELIGIBLE_LOW_BATTERY
    assert evaluate(dev(network_quality=None), POLICY).reason is \
        ReasonCode.SKIPPED_INELIGIBLE_POOR_NETWORK


@pytest.mark.parametrize("battery,expected", [
    (29, False), (30, True), (31, True),
])
def test_battery_threshold_is_inclusive(battery, expected):
    assert evaluate(dev(battery=battery), POLICY).eligible is expected


@pytest.mark.parametrize("quality,expected", [
    (1, False), (2, True), (5, True),
])
def test_network_threshold_is_inclusive(quality, expected):
    assert evaluate(dev(network_quality=quality), POLICY).eligible is expected


# ------------------------------------------------------- the demo devices ---
def test_demo_laptop_d_healthy_devices_are_eligible():
    for device_id, battery, net in [("tcu_D_001", 89, 5), ("tcu_D_002", 85, 4),
                                    ("tcu_D_003", 82, 5)]:
        assert evaluate(dev(device_id=device_id, battery=battery,
                            network_quality=net), POLICY).eligible, device_id


def test_demo_tcu_D_004_is_caught_by_the_battery_gate():
    result = evaluate(dev(device_id="tcu_D_004", battery=8), POLICY)
    assert result.reason is ReasonCode.SKIPPED_INELIGIBLE_LOW_BATTERY


def test_demo_tcu_D_005_is_caught_by_the_network_gate():
    result = evaluate(dev(device_id="tcu_D_005", network_quality=1), POLICY)
    assert result.reason is ReasonCode.SKIPPED_INELIGIBLE_POOR_NETWORK


def test_demo_note_the_gate_alone_would_produce_no_failures():
    """A deliberate record of a real tension in the demo design.

    If the eligibility gate catches D_004 and D_005 first, they are SKIPPED,
    the batch has a 0% failure rate, and the adaptive engine never shrinks --
    so the audience sees nothing happen.

    That is why those two containers run with FAILURE_MODE set: they must be
    offered the update and fail DURING it, producing FAILED_* codes that do
    count. The gate protects devices that are genuinely unfit at offer time;
    the failure modes simulate devices whose condition degrades mid-transfer,
    which is what actually happens to vehicles.
    """
    d004 = evaluate(dev(device_id="tcu_D_004", battery=8), POLICY)
    d005 = evaluate(dev(device_id="tcu_D_005", network_quality=1), POLICY)
    assert not counts_as_failure(d004.reason)
    assert not counts_as_failure(d005.reason)
