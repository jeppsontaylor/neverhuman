"""
testing/test_resource_arbiter.py — Tests for resource arbitration
"""
from core.resource_arbiter import (
    ResourceArbiter, ResourceKind, ResourcePriority,
)


class TestResourceArbiter:
    """Live conversation always wins."""

    def test_register_claim(self):
        arb = ResourceArbiter()
        claim = arb.register_claim(ResourceKind.MIND, "mind-1", ResourcePriority.NORMAL)
        assert not claim.paused
        assert claim.kind == ResourceKind.MIND

    def test_on_user_active_pauses_background(self):
        arb = ResourceArbiter()
        arb.register_claim(ResourceKind.MIND, "mind-1", ResourcePriority.NORMAL)
        arb.register_claim(ResourceKind.FORGE, "forge-1", ResourcePriority.LOW)
        arb.register_claim(ResourceKind.REFLEX, "reflex-1", ResourcePriority.CRITICAL)

        paused = arb.on_user_active()
        assert "mind-1" in paused
        assert "forge-1" in paused
        assert "reflex-1" not in paused

    def test_on_user_idle_resumes(self):
        arb = ResourceArbiter()
        arb.register_claim(ResourceKind.MIND, "mind-1", ResourcePriority.NORMAL)
        arb.on_user_active()
        resumed = arb.on_user_idle()
        assert "mind-1" in resumed

    def test_on_onset_pauses_everything(self):
        arb = ResourceArbiter()
        arb.register_claim(ResourceKind.MIND, "mind-1", ResourcePriority.NORMAL)
        arb.register_claim(ResourceKind.FORGE, "forge-1", ResourcePriority.LOW)
        paused = arb.on_onset()
        assert "mind-1" in paused
        assert "forge-1" in paused

    def test_release_claim(self):
        arb = ResourceArbiter()
        arb.register_claim(ResourceKind.MIND, "mind-1", ResourcePriority.NORMAL)
        arb.release_claim("mind-1")
        status = arb.status()
        assert status["active_claims"] == 0


class TestCircuitBreaker:
    """TTFT circuit breaker pauses all background on degradation."""

    def test_no_circuit_break_initially(self):
        arb = ResourceArbiter()
        assert not arb.circuit_broken

    def test_circuit_breaks_on_high_ttft(self):
        arb = ResourceArbiter()
        arb.register_claim(ResourceKind.MIND, "mind-1", ResourcePriority.NORMAL)
        # Record 10 high TTFT measurements
        for _ in range(10):
            arb.record_ttft(3000.0)  # 3s, above 2s threshold
        assert arb.circuit_broken

    def test_circuit_recovers(self):
        arb = ResourceArbiter()
        # Break it
        for _ in range(10):
            arb.record_ttft(3000.0)
        assert arb.circuit_broken
        # Recover with good measurements
        for _ in range(50):
            arb.record_ttft(500.0)
        assert not arb.circuit_broken

    def test_circuit_broken_blocks_background(self):
        arb = ResourceArbiter()
        for _ in range(10):
            arb.record_ttft(3000.0)
        assert not arb.should_allow(ResourceKind.MIND)
        assert not arb.should_allow(ResourceKind.FORGE)
        assert arb.should_allow(ResourceKind.REFLEX)

    def test_no_resume_while_broken(self):
        arb = ResourceArbiter()
        arb.register_claim(ResourceKind.MIND, "mind-1", ResourcePriority.NORMAL)
        for _ in range(10):
            arb.record_ttft(3000.0)
        resumed = arb.on_user_idle()
        assert len(resumed) == 0  # can't resume while circuit broken

    def test_status_report(self):
        arb = ResourceArbiter()
        arb.register_claim(ResourceKind.MIND, "mind-1", ResourcePriority.NORMAL)
        arb.record_ttft(100.0)
        status = arb.status()
        assert "circuit_broken" in status
        assert "ttft_p95_ms" in status
        assert status["active_claims"] == 1
