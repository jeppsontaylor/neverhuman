"""
testing/test_routerd.py — Tests for the blue-green control plane
"""
import time
from apps.routerd.serve import (
    ControlPlane, Slot, SlotStatus, DaemonRegistry, DaemonInfo,
    ROLLBACK_WINDOW,
)


class TestControlPlane:
    """Blue-green A/B swapping."""

    def test_initial_state(self):
        cp = ControlPlane()
        assert cp.active_slot == "A"
        assert cp.active.name == "A"
        assert cp.standby.name == "B"
        assert not cp.swap_in_progress

    def test_can_swap_when_idle(self):
        cp = ControlPlane()
        cp.slot_b.status = SlotStatus.HEALTHY
        assert cp.can_swap("NONE", "IDLE")

    def test_cannot_swap_when_speaking(self):
        cp = ControlPlane()
        cp.slot_b.status = SlotStatus.HEALTHY
        assert not cp.can_swap("AI", "SPEAKING")

    def test_cannot_swap_when_user_has_floor(self):
        cp = ControlPlane()
        cp.slot_b.status = SlotStatus.HEALTHY
        assert not cp.can_swap("HUMAN", "ENGAGED")

    def test_cannot_swap_when_standby_offline(self):
        cp = ControlPlane()
        assert cp.slot_b.status == SlotStatus.OFFLINE
        assert not cp.can_swap("NONE", "IDLE")

    def test_swap_flow(self):
        cp = ControlPlane()
        cp.slot_a.status = SlotStatus.HEALTHY
        cp.slot_b.status = SlotStatus.HEALTHY
        assert cp.active_slot == "A"

        cp.initiate_swap()
        assert cp.swap_in_progress
        assert cp.slot_a.status == SlotStatus.DRAINING

        cp.slot_a.active_sessions = 0
        assert cp.is_drained()

        cp.complete_swap()
        assert cp.active_slot == "B"
        assert not cp.swap_in_progress
        assert cp.slot_b.status == SlotStatus.HEALTHY
        assert cp.slot_a.status == SlotStatus.STANDBY

    def test_rollback_within_window(self):
        cp = ControlPlane()
        cp.slot_a.status = SlotStatus.HEALTHY
        cp.slot_b.status = SlotStatus.HEALTHY

        # Do initial swap
        cp.initiate_swap()
        cp.complete_swap()
        assert cp.active_slot == "B"

        # Rollback
        cp.standby.status = SlotStatus.HEALTHY
        assert cp.rollback()
        assert cp.active_slot == "A"

    def test_rollback_expired(self):
        cp = ControlPlane()
        cp.slot_a.status = SlotStatus.HEALTHY
        cp.slot_b.status = SlotStatus.HEALTHY
        cp.initiate_swap()
        cp.complete_swap()
        # Fake expired window
        cp.last_swap_at = time.monotonic() - ROLLBACK_WINDOW - 1
        assert not cp.rollback()

    def test_status_report(self):
        cp = ControlPlane()
        report = cp.status_report()
        assert "active_slot" in report
        assert "slot_a" in report
        assert "slot_b" in report


class TestProtocolCompatibility:
    """Candidate must match daemon API versions."""

    def test_compatible_versions(self):
        cp = ControlPlane()
        cp.slot_a.api_version = "4.1"
        cp.slot_b.api_version = "4.1"
        ok, err = cp.check_compatibility()
        assert ok
        assert err is None

    def test_incompatible_versions(self):
        cp = ControlPlane()
        cp.slot_a.api_version = "4.1"
        cp.slot_b.api_version = "4.2"
        ok, err = cp.check_compatibility()
        assert not ok
        assert "mismatch" in err


class TestDaemonRegistry:
    """Daemon registry with version checks."""

    def test_register_and_check(self):
        reg = DaemonRegistry()
        reg.register("llmd", 8088, "1.0", ["generate", "embed"])
        reg.register("asrd", 7864, "1.0", ["transcribe"])

        ok, errors = reg.check_candidate_compat({"llmd": "1.0", "asrd": "1.0"})
        assert ok
        assert len(errors) == 0

    def test_version_mismatch(self):
        reg = DaemonRegistry()
        reg.register("llmd", 8088, "1.0", ["generate"])

        ok, errors = reg.check_candidate_compat({"llmd": "2.0"})
        assert not ok
        assert len(errors) == 1

    def test_missing_daemon(self):
        reg = DaemonRegistry()
        ok, errors = reg.check_candidate_compat({"ttsd": "1.0"})
        assert not ok
