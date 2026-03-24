"""
testing/test_change_router.py — Tests for the change escalation ladder
"""
from core.change_router import (
    ChangeTier, ChangeRequest, classify_change,
)


class TestChangeTierClassification:
    """classify_change routes to the correct tier."""

    def test_live_setting_background(self):
        r = classify_change("Change your background to red")
        assert r.tier == ChangeTier.LIVE_SETTING
        assert not r.needs_reboot

    def test_live_setting_speed(self):
        r = classify_change("respond faster")
        assert r.tier == ChangeTier.LIVE_SETTING

    def test_live_setting_voice(self):
        r = classify_change("change your voice")
        assert r.tier == ChangeTier.LIVE_SETTING

    def test_live_setting_thoughts(self):
        r = classify_change("show me your thoughts")
        assert r.tier == ChangeTier.LIVE_SETTING

    def test_mission_change_focus(self):
        r = classify_change("Focus on science")
        assert r.tier == ChangeTier.MISSION_CHANGE

    def test_mission_change_proactive(self):
        r = classify_change("be more proactive")
        assert r.tier == ChangeTier.MISSION_CHANGE

    def test_code_patch(self):
        r = classify_change("Add a new feature to your system")
        assert r.tier == ChangeTier.CODE_PATCH
        assert r.needs_confirmation

    def test_architecture_change(self):
        r = classify_change("modify your code to change the turn detection architecture")
        assert r.tier == ChangeTier.ARCHITECTURE_CHANGE
        assert r.needs_reboot

    def test_is_code_change(self):
        r = classify_change("modify your code")
        assert r.is_code_change

    def test_default_is_live(self):
        r = classify_change("something unusual")
        assert r.tier == ChangeTier.LIVE_SETTING

    def test_edit_yourself(self):
        r = classify_change("can you edit yourself to add logging")
        assert r.is_code_change
