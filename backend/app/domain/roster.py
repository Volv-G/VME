"""Roster: a collection of players plus optional team metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from .player import Player


class PlayerNotFoundError(Exception):
    """Raised when a player is not found in the roster."""


@dataclass
class Roster:
    """A list of players belonging to a team.

    Supports two on-disk formats:
      - Plain array of player dicts.
      - Object with `team_name`, `team_color`, `players: [...]`.
    """

    players: list[Player] = field(default_factory=list)
    team_name: Optional[str] = None
    team_color: Optional[str] = None

    def __iter__(self) -> Iterator[Player]:
        return iter(self.players)

    def __len__(self) -> int:
        return len(self.players)

    def __bool__(self) -> bool:
        return len(self.players) > 0

    def get_by_number(self, number: int) -> Player:
        for p in self.players:
            if p.number == number:
                return p
        raise PlayerNotFoundError(f"No player with number {number}")

    def find_by_number(self, number: int) -> Optional[Player]:
        for p in self.players:
            if p.number == number:
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_name": self.team_name,
            "team_color": self.team_color,
            "players": [p.to_dict() for p in self.players],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Roster":
        if isinstance(data, list):
            return cls(players=[Player.from_dict(p) for p in data])
        if isinstance(data, dict):
            return cls(
                team_name=data.get("team_name"),
                team_color=data.get("team_color"),
                players=[Player.from_dict(p) for p in data.get("players", [])],
            )
        return cls()

    @classmethod
    def load(cls, path: str | Path) -> "Roster":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
