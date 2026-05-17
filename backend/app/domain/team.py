"""Team enum for home/away designation."""

from enum import Enum


class Team(str, Enum):
    """Home/away team identifier.

    Inherits from str so it serializes naturally to JSON.
    """

    HOME = "home"
    AWAY = "away"

    def other(self) -> "Team":
        return Team.AWAY if self is Team.HOME else Team.HOME
