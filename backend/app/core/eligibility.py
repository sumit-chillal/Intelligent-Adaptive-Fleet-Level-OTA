"""
CONVOY — the eligibility gate.

Before a device is offered firmware, the server asks: is it SAFE to update this
device right now? This is the rule that stops the system bricking a vehicle
that is sitting at 8% battery, and it is the reason a real fleet platform is
more than a file distributor.

Like the adaptive engine, this is a PURE FUNCTION. It takes a snapshot of the
device and the campaign policy and returns a reason code or None. No database,
no clock, no I/O. That makes every rule directly testable and makes the demo's
outcome for tcu_D_004 provable in a unit test rather than hoped for.

THE CRITICAL DISTINCTION
------------------------
Every outcome here is a SKIPPED_* code, never a FAILED_* one.

A device we declined to touch is not evidence that the firmware is bad. If
skips counted toward the failure rate, the adaptive engine would shrink the
batch as punishment for the system's own correct safety decision, and a fleet
parked overnight with low batteries would drive the rollout to a halt for no
reason. See constants.counts_as_failure and Rules.md §3.

ORDER OF CHECKS
---------------
Cheapest and most decisive first: a device already running the target firmware
needs nothing regardless of its battery, and an offline device cannot be
offered anything regardless of everything else.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.constants import ReasonCode


@dataclass(frozen=True)
class DeviceSnapshot:
    """What the gate is allowed to see. Deliberately narrow."""

    device_id: str
    online: bool
    battery: int | None
    network_quality: int | None
    current_version_code: int
    model: str
    seconds_since_last_seen: float | None = None


@dataclass(frozen=True)
class EligibilityPolicy:
    min_battery: int = 30
    min_network_quality: int = 2
    target_version_code: int = 0
    offline_ttl_seconds: float = 20.0
    # A rollback moves devices BACKWARDS. Every version check below has to
    # know which direction "done" is, or a rollback campaign would skip the
    # entire fleet as already-up-to-date.
    is_rollback: bool = False


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reason: ReasonCode | None
    detail: str = ""

    @property
    def skipped(self) -> bool:
        return not self.eligible


ELIGIBLE = EligibilityResult(eligible=True, reason=None)


def evaluate(device: DeviceSnapshot, policy: EligibilityPolicy) -> EligibilityResult:
    """Decide whether this device may receive an offer right now."""

    # 1. Already done. Cheapest possible answer, and it must come first:
    #    re-offering firmware a device already runs would waste a batch slot
    #    and, worse, count as a real attempt in the failure-rate maths.
    #
    #    "Done" means something different for a rollback. A normal campaign is
    #    done when the device is at or above the target; a rollback is done
    #    only when it is exactly AT the target, because a device on a lower
    #    version than the rollback target was never affected by the bad
    #    release and must not be dragged along by the recovery.
    if policy.target_version_code > 0:
        if policy.is_rollback:
            if device.current_version_code == policy.target_version_code:
                return EligibilityResult(
                    False, ReasonCode.SKIPPED_ALREADY_ON_TARGET,
                    f"already on the rollback target "
                    f"(version_code {device.current_version_code})")
            if device.current_version_code < policy.target_version_code:
                return EligibilityResult(
                    False, ReasonCode.SKIPPED_ALREADY_ON_TARGET,
                    f"on version_code {device.current_version_code}, below the "
                    f"rollback target {policy.target_version_code}; never "
                    f"received the version being rolled back")
        elif device.current_version_code >= policy.target_version_code:
            return EligibilityResult(
                False, ReasonCode.SKIPPED_ALREADY_ON_TARGET,
                f"already on version_code {device.current_version_code}")

    # 2. Reachable. An offer to an offline device is a message into the void
    #    that will time out and pollute the batch.
    if not device.online:
        return EligibilityResult(
            False, ReasonCode.SKIPPED_OFFLINE, "device is not connected")

    if (device.seconds_since_last_seen is not None
            and device.seconds_since_last_seen > policy.offline_ttl_seconds):
        return EligibilityResult(
            False, ReasonCode.SKIPPED_OFFLINE,
            f"last seen {device.seconds_since_last_seen:.0f}s ago, "
            f"beyond the {policy.offline_ttl_seconds:.0f}s heartbeat window")

    # 3. Enough power to finish. A device that dies mid-flash with a partially
    #    written slot is the exact failure mode A/B partitioning exists to
    #    prevent -- but prevention is cheaper than recovery.
    if device.battery is None:
        return EligibilityResult(
            False, ReasonCode.SKIPPED_INELIGIBLE_LOW_BATTERY,
            "no battery reading available; refusing to assume it is safe")

    if device.battery < policy.min_battery:
        return EligibilityResult(
            False, ReasonCode.SKIPPED_INELIGIBLE_LOW_BATTERY,
            f"battery {device.battery}% is below the {policy.min_battery}% minimum")

    # 4. Enough link quality to transfer. A weak link does not merely make the
    #    download slow; it makes the transfer likely to stall part-way and
    #    consume a retry slot.
    if device.network_quality is None:
        return EligibilityResult(
            False, ReasonCode.SKIPPED_INELIGIBLE_POOR_NETWORK,
            "no network quality reading available")

    if device.network_quality < policy.min_network_quality:
        return EligibilityResult(
            False, ReasonCode.SKIPPED_INELIGIBLE_POOR_NETWORK,
            f"network quality {device.network_quality} is below the "
            f"minimum of {policy.min_network_quality}")

    return ELIGIBLE