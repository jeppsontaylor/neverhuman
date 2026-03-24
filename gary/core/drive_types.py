"""
core/drive_types.py — DriveVector + AffectVector for mind scheduling

DriveVector controls what the mind prioritizes.
Updated by: mission profile edits, user corrections, quest progress, open loop age.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DriveVector:
    """What GARY is motivated to pursue right now."""
    world_curiosity: float = 0.5
    user_curiosity: float = 0.5
    system_improvement: float = 0.5
    closure_drive: float = 0.3
    novelty_hunger: float = 0.5
    initiative_restraint: float = 0.7  # higher = more restrained

    def for_domain(self, domain: str) -> float:
        """Return drive strength for a quest domain."""
        mapping = {
            "science": self.world_curiosity,
            "system": self.system_improvement,
            "user": self.user_curiosity,
            "self": self.novelty_hunger,
        }
        return mapping.get(domain, 0.5)

    def to_dict(self) -> dict:
        return {
            "world_curiosity": self.world_curiosity,
            "user_curiosity": self.user_curiosity,
            "system_improvement": self.system_improvement,
            "closure_drive": self.closure_drive,
            "novelty_hunger": self.novelty_hunger,
            "initiative_restraint": self.initiative_restraint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DriveVector":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
