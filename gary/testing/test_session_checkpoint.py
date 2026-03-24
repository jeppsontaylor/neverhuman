"""
testing/test_session_checkpoint.py — Tests for session checkpointing
"""
import json
from core.session_checkpoint import SessionCheckpoint, CheckpointStore


class TestSessionCheckpoint:
    """Session checkpoint stores and restores state."""

    def test_create_checkpoint(self):
        cp = SessionCheckpoint(session_id="test-123")
        assert cp.session_id == "test-123"
        assert len(cp.history_window) == 0
        assert cp.floor_owner == "NONE"

    def test_add_turns(self):
        cp = SessionCheckpoint(session_id="test-123")
        cp.add_turn("user", "hello")
        cp.add_turn("assistant", "hi there")
        assert len(cp.history_window) == 2
        assert cp.history_window[0]["role"] == "user"

    def test_history_trim(self):
        cp = SessionCheckpoint(session_id="test-123", max_history=3)
        for i in range(5):
            cp.add_turn("user", f"turn {i}")
        assert len(cp.history_window) == 3
        assert cp.history_window[0]["content"] == "turn 2"

    def test_snapshot_floor(self):
        cp = SessionCheckpoint(session_id="test-123")
        cp.snapshot_floor("AI", "SPEAKING", "NORMAL", "turn-456")
        assert cp.floor_owner == "AI"
        assert cp.engagement == "SPEAKING"
        assert cp.pending_turn_id == "turn-456"

    def test_snapshot_affect(self):
        cp = SessionCheckpoint(session_id="test-123")
        cp.snapshot_affect({"valence": 0.7}, {"curiosity": 0.8})
        assert cp.affect_snapshot["valence"] == 0.7
        assert cp.drive_snapshot["curiosity"] == 0.8

    def test_session_overlay(self):
        cp = SessionCheckpoint(session_id="test-123")
        cp.set_overlay("response_speed", "fast")
        assert cp.session_overlays["response_speed"] == "fast"

    def test_json_roundtrip(self):
        cp = SessionCheckpoint(session_id="test-123")
        cp.add_turn("user", "hello")
        cp.snapshot_floor("AI", "SPEAKING", "NORMAL")
        cp.set_overlay("speed", "fast")

        serialized = cp.to_json()
        restored = SessionCheckpoint.from_json(serialized)

        assert restored.session_id == "test-123"
        assert len(restored.history_window) == 1
        assert restored.floor_owner == "AI"
        assert restored.session_overlays["speed"] == "fast"

    def test_restore_summary(self):
        cp = SessionCheckpoint(session_id="test-123")
        cp.add_turn("user", "hello")
        summary = cp.restore_summary()
        assert "test-123" in summary
        assert "1 turns" in summary


class TestCheckpointStore:
    """In-memory checkpoint store."""

    def test_save_and_load(self):
        store = CheckpointStore()
        cp = SessionCheckpoint(session_id="s1")
        cp.add_turn("user", "hello")
        store.save(cp)

        loaded = store.load("s1")
        assert loaded is not None
        assert loaded.session_id == "s1"

    def test_load_missing(self):
        store = CheckpointStore()
        assert store.load("nonexistent") is None

    def test_delete(self):
        store = CheckpointStore()
        store.save(SessionCheckpoint(session_id="s1"))
        store.delete("s1")
        assert store.load("s1") is None

    def test_list_sessions(self):
        store = CheckpointStore()
        store.save(SessionCheckpoint(session_id="s1"))
        store.save(SessionCheckpoint(session_id="s2"))
        assert sorted(store.list_sessions()) == ["s1", "s2"]
