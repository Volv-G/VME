"""Player data model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class Player:
    """A single player on a roster.

    Attributes:
        name: Full display name.
        number: Jersey number (used as the stable identifier within a roster).
        short_name: Abbreviated name for compact displays (optional).
        profile_pic_path: Path/URL to a profile picture (optional).
    """

    name: str
    number: int
    short_name: Optional[str] = None
    profile_pic_path: Optional[str] = None

    @property
    def display_name(self) -> str:
        return f"#{self.number} {self.short_name or self.name}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Player":
        return cls(
            name=data.get("name", ""),
            number=int(data.get("number", 0)),
            short_name=data.get("short_name"),
            profile_pic_path=data.get("profile_pic_path"),
        )
