"""
Tests for the adaptive rollout engine.

The demo-scenario test at the bottom is the single most important test in the
project: if it fails, the live demonstration fails, because it asserts exactly
the behaviour the audience is told to watch for.
"""

from __future__ import annotations

import pytest

from app.constants import DecisionAction, DecisionReason, ReasonCode
from app.core.adaptive import (
    BatchOutcome,
    RolloutPolicy,
    RolloutState,
    decide,
    initial_state,
    plan_batch_size,
)

OK = ReasonCode.SUCCESS
BAT = ReasonCode.FAILED_LOW_BATTERY
NET = ReasonCode.FAILED_POOR_NETWORK
SKIP = ReasonCode.SKIPPED_INELIGIBLE_LOW_BATTERY

POLICY = RolloutPolicy(
    batch_size_initial=5, batch_size_min=1, batch_size_max=20, canary_size=2,
    shrink_threshold=0.20, abort_threshold=0.40, grow_after_clean_batches=2,
)


# --------------------------------------------------------------- accounting --
def test_skipped_devices_are_excluded_from_the_failure_rate():
    """The most consequential rule in the system.

    Refusing to update a device at 8% battery is correct behaviour. If it were
    counted as a failure, the engine would shrink the batch as punishment for
    its own good judgement.
    """
    batch = BatchOutcome(index=1, outcomes=(OK, OK, OK, SKIP, SKIP))
    assert batch.attempted == 3
    assert batch.failures == 0
    assert batch.skipped == 2
    assert batch.failure_rate == 0.0


def test_failure_rate_is_over_attempted_not_total():
    batch = BatchOutcome(index=1, outcomes=(OK, BAT, SKIP))
    assert batch.attempted == 2
    assert batch.failure_rate == pytest.approx(0.5)


def test_all_skipped_batch_does_not_move_the_batch_size():
    batch = BatchOutcome(index=1, outcomes=(SKIP, SKIP, SKIP))
    d = decide(batch, POLICY, RolloutState(batch_size=5, clean_streak=1))
    assert d.new_batch_size == 5
    assert d.reason is DecisionReason.HOLD_COOLDOWN
    assert d.next_state.clean_streak == 1  # streak preserved, not reset


# ------------------------------------------------------------------ canary ---
def test_first_batch_is_forced_to_canary_size():
    state = initial_state(POLICY)
    assert state.batch_size == 5
    assert plan_batch_size(POLICY, state) == 2  # canary overrides initial size


def test_after_first_batch_the_planned_size_is_the_state_size():
    state = RolloutState(batch_size=5, batches_completed=1)
    assert plan_batch_size(POLICY, state) == 5


# ---------------------------------------------------------------- decisions --
@pytest.mark.parametrize(
    "outcomes,size,expected_size,expected_reason",
    [
        # 40% failure -> abort threshold reached
        ((OK, OK, OK, BAT, NET), 5, 5, DecisionReason.ABORT_FAILURE_STORM),
        # 25% -> above shrink threshold, halve (floor)
        ((OK, OK, OK, BAT), 4, 2, DecisionReason.SHRINK_HIGH_FAILURE),
        # 20% exactly -> boundary is inclusive, halve
        ((OK, OK, OK, OK, BAT), 5, 2, DecisionReason.SHRINK_HIGH_FAILURE),
        # 12.5% -> below shrink threshold but non-zero, back off by one
        ((OK,) * 7 + (BAT,), 8, 7, DecisionReason.SHRINK_MINOR_FAILURE),
        # clean, streak not yet met -> hold
        ((OK, OK, OK), 3, 3, DecisionReason.HOLD_COOLDOWN),
    ],
)
def test_decision_table(outcomes, size, expected_size, expected_reason):
    d = decide(BatchOutcome(1, outcomes), POLICY, RolloutState(batch_size=size))
    assert d.new_batch_size == expected_size
    assert d.reason is expected_reason


def test_growth_requires_consecutive_clean_batches():
    state = RolloutState(batch_size=4, clean_streak=0)
    clean = BatchOutcome(1, (OK, OK, OK, OK))

    first = decide(clean, POLICY, state)
    assert first.reason is DecisionReason.HOLD_COOLDOWN
    assert first.new_batch_size == 4
    assert first.next_state.clean_streak == 1

    second = decide(clean, POLICY, first.next_state)
    assert second.reason is DecisionReason.GROW_STABLE
    assert second.new_batch_size == 6  # ceil(4 * 1.5)


def test_a_single_failure_resets_the_clean_streak():
    state = RolloutState(batch_size=4, clean_streak=1)
    d = decide(BatchOutcome(1, (OK, OK, OK, BAT)), POLICY, state)
    assert d.next_state.clean_streak == 0


def test_batch_size_never_falls_below_the_minimum():
    """Note the batch here has 5 attempted devices while the *state* batch size
    is already 1 -- otherwise a lone failure would be a 100% failure rate and
    would correctly trigger the abort guard instead, which is a different code
    path (see test_a_single_device_batch_that_fails_aborts)."""
    d = decide(BatchOutcome(1, (OK, OK, OK, OK, BAT)), POLICY, RolloutState(batch_size=1))
    assert d.observed_failure_rate == pytest.approx(0.20)
    assert d.new_batch_size == POLICY.batch_size_min == 1
    assert d.reason is DecisionReason.HOLD_AT_MIN


def test_a_single_device_batch_that_fails_aborts():
    """100% failure is unambiguous: stop, do not merely shrink."""
    d = decide(BatchOutcome(1, (BAT,)), POLICY, RolloutState(batch_size=1))
    assert d.observed_failure_rate == pytest.approx(1.0)
    assert d.action is DecisionAction.ABORT


def test_batch_size_never_exceeds_the_maximum():
    policy = RolloutPolicy(batch_size_max=6, grow_after_clean_batches=1)
    d = decide(BatchOutcome(1, (OK,) * 5), policy, RolloutState(batch_size=5, clean_streak=1))
    assert d.new_batch_size == 6


def test_abort_action_is_distinct_from_shrink():
    d = decide(BatchOutcome(1, (BAT, BAT, OK, OK, OK)), POLICY, RolloutState(batch_size=5))
    assert d.action is DecisionAction.ABORT
    d2 = decide(BatchOutcome(1, (BAT, OK, OK, OK)), POLICY, RolloutState(batch_size=4))
    assert d2.action is DecisionAction.CONTINUE


# -------------------------------------------------------------------- ewma ---
def test_ewma_smooths_across_batches():
    state = RolloutState(batch_size=5, ewma_failure_rate=0.0)
    d = decide(BatchOutcome(1, (OK, OK, OK, BAT)), POLICY, state)  # rate 0.25
    assert d.ewma_failure_rate == pytest.approx(0.125)  # 0.5*0.25 + 0.5*0.0
    d2 = decide(BatchOutcome(2, (OK, OK)), POLICY, d.next_state)   # rate 0.0
    assert d2.ewma_failure_rate == pytest.approx(0.0625)


# ---------------------------------------------------------- purity guarantee -
def test_decide_is_deterministic_and_does_not_mutate_inputs():
    state = RolloutState(batch_size=5, clean_streak=1, ewma_failure_rate=0.3)
    batch = BatchOutcome(1, (OK, OK, OK, BAT))
    a = decide(batch, POLICY, state)
    b = decide(batch, POLICY, state)
    assert a == b
    assert state.batch_size == 5 and state.clean_streak == 1  # untouched


# ------------------------------------------------------------ THE DEMO TEST --
def test_demo_scenario_batch_shrinks_from_five_to_two():
    """Success Criterion 4, asserted.

    Batch 02 contains tcu_D_004 (battery 8%) and tcu_D_005 (network quality 1)
    alongside three healthy devices. Two of five attempted devices fail, which
    is 40% -- and 40% is the abort threshold, not merely the shrink threshold.

    This test documents a real tension in the demo configuration: with the
    default abort_threshold of 0.40, the scripted failure pattern ABORTS the
    campaign instead of shrinking it. The demo therefore runs with
    abort_threshold raised to 0.50, so that 40% shrinks 5 -> 2 and the audience
    sees adaptation rather than a halt.
    """
    demo_policy = RolloutPolicy(
        batch_size_initial=5, batch_size_min=1, batch_size_max=20, canary_size=2,
        shrink_threshold=0.20, abort_threshold=0.50, grow_after_clean_batches=2,
    )
    batch = BatchOutcome(index=2, outcomes=(OK, OK, OK, BAT, NET))

    d = decide(batch, demo_policy, RolloutState(batch_size=5, batches_completed=1))

    assert d.observed_failure_rate == pytest.approx(0.40)
    assert d.action is DecisionAction.CONTINUE
    assert d.reason is DecisionReason.SHRINK_HIGH_FAILURE
    assert d.previous_batch_size == 5
    assert d.new_batch_size == 2

    banner = d.banner("c_7f21", 2)
    assert "SHRINK_HIGH_FAILURE" in banner
    assert "5 -> 2" in banner
    assert "40.0%" in banner
    print("\n" + banner)


def test_demo_scenario_with_default_policy_aborts():
    """The same batch under the DEFAULT policy aborts. Both behaviours are
    correct; the difference is risk appetite, and it is configuration."""
    d = decide(BatchOutcome(2, (OK, OK, OK, BAT, NET)), POLICY,
               RolloutState(batch_size=5, batches_completed=1))
    assert d.action is DecisionAction.ABORT
    assert d.reason is DecisionReason.ABORT_FAILURE_STORM


def test_full_campaign_trajectory():
    """End-to-end: canary, failure spike, shrink, recovery, growth."""
    policy = RolloutPolicy(batch_size_initial=5, batch_size_min=1, batch_size_max=10,
                           canary_size=2, abort_threshold=0.50, grow_after_clean_batches=2)
    state = initial_state(policy)
    sizes: list[int] = []

    # batch 1: canary of 2, clean
    assert plan_batch_size(policy, state) == 2
    state = decide(BatchOutcome(1, (OK, OK)), policy, state).next_state
    sizes.append(state.batch_size)

    # batch 2: 5 devices, 2 fail -> shrink to 2
    state = decide(BatchOutcome(2, (OK, OK, OK, BAT, NET)), policy, state).next_state
    sizes.append(state.batch_size)

    # batches 3 and 4: clean -> hold, then grow
    state = decide(BatchOutcome(3, (OK, OK)), policy, state).next_state
    sizes.append(state.batch_size)
    state = decide(BatchOutcome(4, (OK, OK)), policy, state).next_state
    sizes.append(state.batch_size)

    assert sizes == [5, 2, 2, 3]
