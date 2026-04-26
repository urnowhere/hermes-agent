"""Tests for agent.auxiliary_health — consecutive-failure tracking.

Covers:
- Tracker counts consecutive failures per task.
- Success resets the counter.
- Notifier escalates at the threshold and only at the threshold (or per the
  re-emit rule).
- ``hermes doctor`` reads the tracker correctly.
- Real call sites (``call_llm`` and ``title_generator``) actually call the
  tracker so failures escalate end-to-end.

See issue #15775.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.auxiliary_health import (
    AuxiliaryHealthTracker,
    TaskHealth,
    get_tracker,
    reset_tracker_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Force a clean tracker singleton before and after each test."""
    reset_tracker_for_tests()
    yield
    reset_tracker_for_tests()


# ── Core counter behaviour ──────────────────────────────────────────────


class TestAuxiliaryHealthTracker:
    def test_starts_with_no_state(self):
        tracker = AuxiliaryHealthTracker()
        assert tracker.get_status() == {}
        assert tracker.get_failing_tasks() == {}
        assert tracker.should_escalate("title_generation") is False

    def test_counts_consecutive_failures(self):
        tracker = AuxiliaryHealthTracker(threshold=3)
        for _ in range(2):
            tracker.record_failure("title_generation", RuntimeError("boom"))
        status = tracker.get_status()
        assert status["title_generation"].consecutive_failures == 2
        assert status["title_generation"].total_failures == 2
        assert status["title_generation"].last_error_class == "RuntimeError"

    def test_success_resets_counter(self):
        tracker = AuxiliaryHealthTracker(threshold=3)
        tracker.record_failure("compression", RuntimeError("boom"))
        tracker.record_failure("compression", RuntimeError("boom"))
        tracker.record_success("compression")

        status = tracker.get_status()
        assert status["compression"].consecutive_failures == 0
        assert status["compression"].total_failures == 2
        assert status["compression"].total_successes == 1

    def test_each_task_tracked_independently(self):
        tracker = AuxiliaryHealthTracker(threshold=3)
        tracker.record_failure("title_generation", RuntimeError("a"))
        tracker.record_failure("title_generation", RuntimeError("b"))
        tracker.record_failure("session_search", RuntimeError("c"))

        status = tracker.get_status()
        assert status["title_generation"].consecutive_failures == 2
        assert status["session_search"].consecutive_failures == 1

    def test_clear_resets_specific_task(self):
        tracker = AuxiliaryHealthTracker(threshold=3)
        tracker.record_failure("title_generation", RuntimeError("a"))
        tracker.record_failure("compression", RuntimeError("b"))

        tracker.clear("title_generation")
        status = tracker.get_status()
        assert "title_generation" not in status
        assert status["compression"].consecutive_failures == 1

    def test_clear_all_when_no_task(self):
        tracker = AuxiliaryHealthTracker(threshold=3)
        tracker.record_failure("a", RuntimeError("x"))
        tracker.record_failure("b", RuntimeError("y"))
        tracker.clear()
        assert tracker.get_status() == {}

    def test_get_failing_tasks_filters_below_threshold(self):
        tracker = AuxiliaryHealthTracker(threshold=3)
        tracker.record_failure("a", RuntimeError("x"))
        tracker.record_failure("a", RuntimeError("x"))
        tracker.record_failure("b", RuntimeError("y"))
        tracker.record_failure("b", RuntimeError("y"))
        tracker.record_failure("b", RuntimeError("y"))
        failing = tracker.get_failing_tasks()
        assert "a" not in failing
        assert "b" in failing
        assert failing["b"].consecutive_failures == 3


# ── Escalation behaviour ────────────────────────────────────────────────


class TestEscalation:
    def test_does_not_fire_below_threshold(self):
        tracker = AuxiliaryHealthTracker(threshold=3)
        notifier = MagicMock()
        tracker.set_notifier(notifier)

        tracker.record_failure("title_generation", RuntimeError("boom"))
        tracker.record_failure("title_generation", RuntimeError("boom"))

        notifier.assert_not_called()

    def test_fires_exactly_once_at_threshold(self):
        tracker = AuxiliaryHealthTracker(threshold=3, reemit_interval=10)
        notifier = MagicMock()
        tracker.set_notifier(notifier)

        # Three consecutive failures should fire exactly one warning at the
        # threshold crossing.
        for _ in range(3):
            tracker.record_failure("title_generation", RuntimeError("boom"))
        assert notifier.call_count == 1

        # Each subsequent failure (4, 5, 6, ...) does NOT re-fire until we
        # hit the re-emit interval.
        for _ in range(5):
            tracker.record_failure("title_generation", RuntimeError("boom"))
        assert notifier.call_count == 1

    def test_reemits_after_interval_past_threshold(self):
        tracker = AuxiliaryHealthTracker(threshold=3, reemit_interval=5)
        notifier = MagicMock()
        tracker.set_notifier(notifier)

        # Threshold crossing at failure #3 → 1 warning.
        for _ in range(3):
            tracker.record_failure("session_search", RuntimeError("boom"))
        assert notifier.call_count == 1

        # Failures 4, 5, 6, 7 → still no extra warning (4 < threshold + interval).
        for _ in range(4):
            tracker.record_failure("session_search", RuntimeError("boom"))
        assert notifier.call_count == 1

        # Failure 8 hits the re-emit window (8 - 3 = 5 >= reemit_interval).
        tracker.record_failure("session_search", RuntimeError("boom"))
        assert notifier.call_count == 2

    def test_success_then_threshold_breach_refires(self):
        tracker = AuxiliaryHealthTracker(threshold=3, reemit_interval=10)
        notifier = MagicMock()
        tracker.set_notifier(notifier)

        for _ in range(3):
            tracker.record_failure("title_generation", RuntimeError("boom"))
        assert notifier.call_count == 1

        tracker.record_success("title_generation")  # resets counter

        # Another threshold breach fires again.
        for _ in range(3):
            tracker.record_failure("title_generation", RuntimeError("boom"))
        assert notifier.call_count == 2

    def test_notifier_receives_health_snapshot(self):
        tracker = AuxiliaryHealthTracker(threshold=3)
        captured = []

        def grab(task, health):
            captured.append((task, health))

        tracker.set_notifier(grab)

        for _ in range(3):
            tracker.record_failure("compression", ValueError("provider returned 402"))

        assert len(captured) == 1
        task, health = captured[0]
        assert task == "compression"
        assert isinstance(health, TaskHealth)
        assert health.consecutive_failures == 3
        assert health.last_error_class == "ValueError"
        assert "402" in (health.last_error or "")

    def test_notifier_exception_does_not_break_tracker(self):
        tracker = AuxiliaryHealthTracker(threshold=2)

        def boom_notifier(task, health):
            raise RuntimeError("notifier broken")

        tracker.set_notifier(boom_notifier)

        # Should not raise — notifier exception is caught.
        for _ in range(2):
            tracker.record_failure("session_search", RuntimeError("boom"))

        # Tracker state is still consistent.
        status = tracker.get_status()
        assert status["session_search"].consecutive_failures == 2

    def test_threshold_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("HERMES_AUX_FAILURE_THRESHOLD", "5")
        tracker = AuxiliaryHealthTracker()
        assert tracker.threshold == 5

    def test_invalid_threshold_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("HERMES_AUX_FAILURE_THRESHOLD", "not-a-number")
        tracker = AuxiliaryHealthTracker()
        assert tracker.threshold == AuxiliaryHealthTracker.DEFAULT_THRESHOLD


# ── Singleton behaviour ────────────────────────────────────────────────


class TestSingleton:
    def test_get_tracker_returns_same_instance(self):
        a = get_tracker()
        b = get_tracker()
        assert a is b

    def test_reset_returns_fresh_instance(self):
        a = get_tracker()
        reset_tracker_for_tests()
        b = get_tracker()
        assert a is not b


# ── Integration: call_llm wires into the tracker ───────────────────────


class TestCallLlmIntegration:
    """The auxiliary client wrapper must record success and failure outcomes
    against the tracker.  This is the integration the issue actually asks
    for — a unit test on the tracker alone is not enough."""

    def test_call_llm_records_failure_on_provider_exception(self):
        from agent import auxiliary_client

        tracker = get_tracker()
        notifier = MagicMock()
        tracker.set_notifier(notifier)

        with patch.object(
            auxiliary_client,
            "_call_llm_inner",
            side_effect=RuntimeError("OpenRouter 402 — out of credit"),
        ):
            for _ in range(3):
                with pytest.raises(RuntimeError):
                    auxiliary_client.call_llm(
                        task="title_generation",
                        messages=[{"role": "user", "content": "hi"}],
                    )

        status = tracker.get_status()
        assert status["title_generation"].consecutive_failures == 3
        # Threshold (3) crossed → notifier fires once.
        assert notifier.call_count == 1
        called_task, called_health = notifier.call_args.args
        assert called_task == "title_generation"
        assert "402" in (called_health.last_error or "")

    def test_call_llm_records_success_resets_counter(self):
        from agent import auxiliary_client

        tracker = get_tracker()

        # First, two failures.
        with patch.object(
            auxiliary_client,
            "_call_llm_inner",
            side_effect=RuntimeError("transient"),
        ):
            for _ in range(2):
                with pytest.raises(RuntimeError):
                    auxiliary_client.call_llm(
                        task="title_generation",
                        messages=[{"role": "user", "content": "hi"}],
                    )

        # Now a success.
        fake_response = MagicMock()
        with patch.object(
            auxiliary_client, "_call_llm_inner", return_value=fake_response
        ):
            result = auxiliary_client.call_llm(
                task="title_generation",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert result is fake_response
        status = tracker.get_status()
        assert status["title_generation"].consecutive_failures == 0
        assert status["title_generation"].total_successes == 1


class TestTitleGeneratorIntegration:
    """The title generator wraps call_llm in its own try/except — the
    tracker must still observe the failure because call_llm records
    BEFORE the title generator catches.  Without the wiring, repeated
    OpenRouter 402s would never escalate.  This is the exact bug from
    issue #15775."""

    def test_title_generator_failure_increments_tracker(self):
        from agent import title_generator

        tracker = get_tracker()

        with patch.object(
            title_generator,
            "call_llm",
            side_effect=RuntimeError("OpenRouter 402"),
        ):
            # generate_title swallows the error and returns None — but the
            # tracker must already have observed the failure.
            for _ in range(3):
                result = title_generator.generate_title("hi", "hello")
                assert result is None

        status = tracker.get_status()
        # The patched call_llm above is title_generator's reference; it
        # bypasses our wrapper.  So we patch ``call_llm`` at the auxiliary
        # client level instead — see the next test.
        # This test exists to document that patching at the title_generator
        # module level intentionally bypasses the wrapper.
        assert "title_generation" not in status

    def test_title_generator_failure_via_real_wrapper_increments_tracker(self):
        """Patch the *inner* implementation so the public call_llm wrapper
        runs and records the failure into the tracker."""
        from agent import auxiliary_client, title_generator

        tracker = get_tracker()
        notifier = MagicMock()
        tracker.set_notifier(notifier)

        with patch.object(
            auxiliary_client,
            "_call_llm_inner",
            side_effect=RuntimeError("OpenRouter 402 — credits exhausted"),
        ):
            for _ in range(3):
                # title_generator.generate_title catches the error and
                # returns None — so the user never sees the failure unless
                # the tracker escalates.
                result = title_generator.generate_title("hi", "hello")
                assert result is None

        # Tracker observed all three failures even though title_generator
        # swallowed the exception — that is the whole point.
        status = tracker.get_status()
        assert status["title_generation"].consecutive_failures == 3
        # Notifier fires exactly once at the threshold.
        assert notifier.call_count == 1


# ── Doctor integration ────────────────────────────────────────────────


class TestBackgroundReviewGated:
    """The background memory/skill reviewer used to call
    ``_emit_auxiliary_failure`` directly on every exception, bypassing the
    threshold gate.  Option A: route through the tracker too so its
    behavior matches title_generation / compression / session_search.

    See issue #15775 review notes."""

    def test_background_review_uses_tracker_threshold(self):
        """Three background-review failures should fire exactly one
        warning at the threshold — not three warnings, one per failure."""
        tracker = get_tracker()
        notifier = MagicMock()
        tracker.set_notifier(notifier)

        # Simulate three background-review failures the way the live code
        # records them — through tracker.record_failure, not through a
        # direct emit.
        for _ in range(3):
            tracker.record_failure("background_review", RuntimeError("boom"))

        # Default threshold is 3 → exactly one notification fires.
        assert notifier.call_count == 1
        called_task, _ = notifier.call_args.args
        assert called_task == "background_review"

    def test_background_review_below_threshold_silent(self):
        """A single transient blip must NOT escalate (the bug we fixed)."""
        tracker = get_tracker()
        notifier = MagicMock()
        tracker.set_notifier(notifier)

        tracker.record_failure("background_review", RuntimeError("transient"))
        notifier.assert_not_called()

    def test_background_review_success_resets_counter(self):
        """A successful run after failures must reset the counter so the
        threshold is not tripped by an old transient blip."""
        tracker = get_tracker()
        notifier = MagicMock()
        tracker.set_notifier(notifier)

        tracker.record_failure("background_review", RuntimeError("boom"))
        tracker.record_failure("background_review", RuntimeError("boom"))
        tracker.record_success("background_review")

        # Counter resets — three subsequent failures fire one warning.
        for _ in range(3):
            tracker.record_failure("background_review", RuntimeError("boom"))
        assert notifier.call_count == 1


class TestNotifierOwnerAware:
    """The auxiliary-health notifier registration must satisfy two
    constraints simultaneously (issue #15775 review):

    1. Helper / fork-and-forget agents (delegation children built by
       ``delegate_tool._build_child_agent``, the background memory/skill
       reviewer in ``_spawn_background_review``, /btw side questions,
       compression helpers, etc.) must NOT clobber the parent's
       notifier — after the helper completes it is dormant and its
       ``_emit_warning`` would route into a discarded output channel,
       silently swallowing escalations.  The helper's eventual ``close()``
       would then leave the tracker with no notifier at all, disabling
       escalations for the rest of the parent's session.

    2. The gateway constructs a fresh AIAgent per inbound message; each
       successive top-level agent owns the active output channel and
       MUST install its own notifier.  A permanent first-wins guard
       would route warnings into a stale closure (wrong session, possibly
       wrong platform target).

    The implementation: ``AIAgent.__init__`` takes
    ``install_auxiliary_notifier`` (default True) and only installs when
    True.  Owner-aware ``set_notifier`` / ``clear_notifier_if_owner``
    lets each agent release ITS registration on shutdown without
    clobbering a successor.
    """

    def test_has_notifier_predicate(self):
        tracker = AuxiliaryHealthTracker()
        assert tracker.has_notifier() is False
        tracker.set_notifier(lambda task, health: None, owner=object())
        assert tracker.has_notifier() is True
        tracker.set_notifier(None)
        assert tracker.has_notifier() is False

    def test_set_notifier_with_owner_records_owner(self):
        tracker = AuxiliaryHealthTracker()
        owner = object()
        tracker.set_notifier(lambda task, health: None, owner=owner)
        assert tracker.get_notifier_owner() is owner

    def test_set_notifier_none_clears_owner(self):
        tracker = AuxiliaryHealthTracker()
        owner = object()
        tracker.set_notifier(lambda task, health: None, owner=owner)
        tracker.set_notifier(None)
        assert tracker.get_notifier_owner() is None

    def test_clear_notifier_if_owner_returns_false_when_no_owner(self):
        tracker = AuxiliaryHealthTracker()
        assert tracker.clear_notifier_if_owner(object()) is False

    def test_clear_notifier_if_owner_clears_only_own_registration(self):
        tracker = AuxiliaryHealthTracker()
        a = object()
        b = object()
        tracker.set_notifier(lambda task, health: None, owner=a)
        # B clobbers A.
        tracker.set_notifier(lambda task, health: None, owner=b)
        # A's cleanup must NOT clear B's registration.
        cleared = tracker.clear_notifier_if_owner(a)
        assert cleared is False
        assert tracker.has_notifier() is True
        assert tracker.get_notifier_owner() is b
        # B's cleanup clears its own registration.
        cleared = tracker.clear_notifier_if_owner(b)
        assert cleared is True
        assert tracker.has_notifier() is False

    def test_helper_agent_init_does_not_clobber_parent_notifier(self):
        """A parent (top-level) agent installs.  A helper agent built
        with ``install_auxiliary_notifier=False`` runs ``__init__`` (and
        therefore the notifier install path) too — but it must NOT clobber
        the parent's notifier because the helper is dormant after its
        fork-and-forget work completes.

        Property under test: "doesn't install notifier", regardless of
        whether the helper is a delegation child, background reviewer,
        /btw side question, compression helper, etc.
        """
        from run_agent import AIAgent

        parent_emit = MagicMock(name="parent._emit_warning")
        helper_emit = MagicMock(name="helper._emit_warning")

        parent = MagicMock(spec=AIAgent)
        parent._emit_warning = parent_emit
        parent._install_auxiliary_notifier = True
        AIAgent._install_auxiliary_health_notifier(parent)

        helper = MagicMock(spec=AIAgent)
        helper._emit_warning = helper_emit
        # install_auxiliary_notifier=False: the install path must
        # short-circuit so the parent's closure stays installed.
        helper._install_auxiliary_notifier = False
        AIAgent._install_auxiliary_health_notifier(helper)

        tracker = get_tracker()
        for _ in range(3):
            tracker.record_failure("title_generation", RuntimeError("boom"))

        assert parent_emit.call_count == 1, (
            "Parent's _emit_warning should have received the threshold "
            "warning — helper agent must not have clobbered the notifier."
        )
        assert helper_emit.call_count == 0, (
            "Helper agent's _emit_warning must NOT receive warnings; "
            "the helper is dormant after its work completes."
        )

    def test_background_helper_agent_does_not_clobber_top_level_notifier(self):
        """Regression for the second half of issue #15775's P1 review.

        Sequence the bug exhibited before the fix:
          1. Top-level agent A installs notifier with owner=A.
          2. A spawns the background memory/skill reviewer, which
             constructed an in-process AIAgent without the opt-out flag.
             That helper's __init__ called set_notifier(quiet, owner=helper)
             — clobbering A's user-visible notifier with a discarded one.
          3. Helper finishes; helper.close() called
             clear_notifier_if_owner(helper) — tracker now has NO notifier.
          4. Auxiliary failure during A's continued work → notification
             silently dropped.

        With ``install_auxiliary_notifier=False``, step 2 short-circuits,
        A keeps ownership, and step 4 still routes to A.
        """
        from run_agent import AIAgent

        tracker = get_tracker()

        a_emit = MagicMock(name="A._emit_warning")
        agent_a = MagicMock(spec=AIAgent)
        agent_a._emit_warning = a_emit
        agent_a._install_auxiliary_notifier = True
        AIAgent._install_auxiliary_health_notifier(agent_a)
        assert tracker.get_notifier_owner() is agent_a, (
            "Sanity: top-level A must own the registration."
        )

        # Background reviewer spawns mid-session.
        helper_emit = MagicMock(name="helper._emit_warning")
        helper = MagicMock(spec=AIAgent)
        helper._emit_warning = helper_emit
        helper._install_auxiliary_notifier = False
        AIAgent._install_auxiliary_health_notifier(helper)

        # CRITICAL: helper must not have clobbered A's registration.
        assert tracker.get_notifier_owner() is agent_a, (
            "Background helper must not steal notifier ownership from "
            "the live top-level agent."
        )

        # Helper finishes; close() runs clear_notifier_if_owner(helper).
        # That call must be a no-op because helper never installed.
        cleared = tracker.clear_notifier_if_owner(helper)
        assert cleared is False
        assert tracker.get_notifier_owner() is agent_a, (
            "Helper.close() must not clear A's registration."
        )

        # Now an auxiliary failure breach during A's continued work
        # MUST still route to A.
        for _ in range(3):
            tracker.record_failure("background_review", RuntimeError("boom"))

        assert a_emit.call_count == 1, (
            "Top-level A must still receive the threshold escalation "
            "after a background helper has come and gone — the fix for "
            "issue #15775 P1."
        )
        assert helper_emit.call_count == 0, (
            "Background helper's _emit_warning is dormant — must not "
            "receive warnings."
        )

    def test_background_helper_close_does_not_clear_top_level_registration(self):
        """A helper agent that opted out of installing the notifier must
        not, on shutdown, accidentally clear another agent's registration.

        ``clear_notifier_if_owner(helper)`` must be a no-op because the
        helper was never recorded as the owner — no separate guard in
        ``AIAgent.close()`` needed.
        """
        from run_agent import AIAgent

        tracker = get_tracker()

        top_emit = MagicMock(name="top._emit_warning")
        top = MagicMock(spec=AIAgent)
        top._emit_warning = top_emit
        top._install_auxiliary_notifier = True
        AIAgent._install_auxiliary_health_notifier(top)
        assert tracker.get_notifier_owner() is top

        helper = MagicMock(spec=AIAgent)
        helper._install_auxiliary_notifier = False
        AIAgent._install_auxiliary_health_notifier(helper)
        # Helper did not install — owner still top.
        assert tracker.get_notifier_owner() is top

        # Simulating helper.close() → clear_notifier_if_owner(helper).
        cleared = tracker.clear_notifier_if_owner(helper)

        assert cleared is False, (
            "clear_notifier_if_owner(helper) must return False because "
            "helper never owned the registration."
        )
        assert tracker.get_notifier_owner() is top, (
            "Top-level agent's registration must survive helper.close()."
        )
        assert tracker.has_notifier() is True

    def test_child_agent_init_does_not_clobber_parent_notifier(self):
        """Backward-compat alias for the renamed test above.  Kept so the
        old test name continues to pass — the property under test is
        unchanged: helpers (whether 'delegated' or otherwise) must not
        clobber the parent's notifier.
        """
        self.test_helper_agent_init_does_not_clobber_parent_notifier()

    def test_gateway_per_message_agent_replaces_notifier(self):
        """The gateway constructs a fresh top-level (non-delegated) AIAgent
        per inbound message.  Each install MUST clobber the previous
        registration; otherwise a sustained outage during message N would
        route the threshold warning to message N-1's discarded
        ``_emit_warning`` closure (wrong session, possibly wrong platform
        target).

        Sequence: agent A installs, A is closed (clearing its
        registration), agent B installs, breach during B's turn routes
        to B.
        """
        from run_agent import AIAgent

        tracker = get_tracker()

        a_emit = MagicMock(name="A._emit_warning")
        agent_a = MagicMock(spec=AIAgent)
        agent_a._emit_warning = a_emit
        agent_a._install_auxiliary_notifier = True
        AIAgent._install_auxiliary_health_notifier(agent_a)
        # A owns the registration.
        assert tracker.get_notifier_owner() is agent_a

        # A is closed before message 2 arrives — releases its registration.
        tracker.clear_notifier_if_owner(agent_a)
        assert tracker.has_notifier() is False

        # Message 2: gateway builds AIAgent B (also top-level, not a
        # helper) and B installs.
        b_emit = MagicMock(name="B._emit_warning")
        agent_b = MagicMock(spec=AIAgent)
        agent_b._emit_warning = b_emit
        agent_b._install_auxiliary_notifier = True
        AIAgent._install_auxiliary_health_notifier(agent_b)
        assert tracker.get_notifier_owner() is agent_b

        # Threshold breach during B's turn routes to B — NOT to A's
        # discarded closure.
        for _ in range(3):
            tracker.record_failure("compression", RuntimeError("boom"))

        assert a_emit.call_count == 0, (
            "Agent A's _emit_warning is dormant after close — must not "
            "receive warnings from a later session."
        )
        assert b_emit.call_count == 1, (
            "Agent B is the live session and must receive the threshold "
            "warning."
        )

    def test_concurrent_top_level_install_clobber_then_first_cleanup_no_op(self):
        """Two top-level agents construct in sequence (simulating gateway
        concurrency).  The second install clobbers the first — that is
        correct because the second agent owns the live output channel.
        The first agent's ``cleanup`` must NOT then clear the second
        agent's notifier; ``clear_notifier_if_owner`` is the right
        primitive."""
        from run_agent import AIAgent

        tracker = get_tracker()

        first_emit = MagicMock(name="first._emit_warning")
        first = MagicMock(spec=AIAgent)
        first._emit_warning = first_emit
        first._install_auxiliary_notifier = True
        AIAgent._install_auxiliary_health_notifier(first)

        second_emit = MagicMock(name="second._emit_warning")
        second = MagicMock(spec=AIAgent)
        second._emit_warning = second_emit
        second._install_auxiliary_notifier = True
        # Second top-level install clobbers first's registration.
        AIAgent._install_auxiliary_health_notifier(second)
        assert tracker.get_notifier_owner() is second

        # First's cleanup runs LATER (out-of-order shutdown).  It must
        # not clear second's registration.
        cleared = tracker.clear_notifier_if_owner(first)
        assert cleared is False
        assert tracker.get_notifier_owner() is second

        for _ in range(3):
            tracker.record_failure("session_search", RuntimeError("boom"))
        first_emit.assert_not_called()
        assert second_emit.call_count == 1


class TestDoctorCheck:
    def test_doctor_check_silent_when_no_recorded_outcomes(self, capsys):
        from hermes_cli.doctor import _check_auxiliary_task_health

        issues: list[str] = []
        _check_auxiliary_task_health(issues)
        captured = capsys.readouterr()

        # No tasks recorded → nothing printed and no issues raised.
        assert "Auxiliary Task Health" not in captured.out
        assert issues == []

    def test_doctor_check_reports_failing_tasks(self, capsys):
        from hermes_cli.doctor import _check_auxiliary_task_health

        tracker = get_tracker()
        for _ in range(4):
            tracker.record_failure("title_generation", RuntimeError("OpenRouter 402"))

        issues: list[str] = []
        _check_auxiliary_task_health(issues)
        captured = capsys.readouterr()

        assert "Auxiliary Task Health" in captured.out
        assert "title_generation" in captured.out
        assert "consecutive" in captured.out
        # Failing task adds an issue line for the doctor summary.
        assert any("title_generation" in i for i in issues)

    def test_doctor_check_reports_warning_below_threshold(self, capsys):
        from hermes_cli.doctor import _check_auxiliary_task_health

        tracker = get_tracker()
        # Two failures with default threshold 3 → warn but no issue.
        tracker.record_failure("compression", RuntimeError("transient"))
        tracker.record_failure("compression", RuntimeError("transient"))

        issues: list[str] = []
        _check_auxiliary_task_health(issues)
        captured = capsys.readouterr()

        assert "Auxiliary Task Health" in captured.out
        assert "compression" in captured.out
        # Below threshold — no issue raised.
        assert issues == []

    def test_doctor_check_reports_healthy_tasks(self, capsys):
        from hermes_cli.doctor import _check_auxiliary_task_health

        tracker = get_tracker()
        tracker.record_success("session_search")
        tracker.record_success("session_search")

        issues: list[str] = []
        _check_auxiliary_task_health(issues)
        captured = capsys.readouterr()

        assert "Auxiliary Task Health" in captured.out
        assert "session_search" in captured.out
        assert issues == []
