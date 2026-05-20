"""Roster: a collection of players plus optional team metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from .player import Player


class PlayerNotFoundError(Exception):
    """Raised when a player is not found in the roster."""


# Default templates used when the team profile doesn't override them.
# Format placeholders: {date}, {team}, {opponent}, {tournament_abbr},
# {tournament_full}, {match_index}. (See app.upload.templates.)
DEFAULT_YT_TITLE_TEMPLATE = (
    "{date}. {tournament_abbr}. M{match_index}. {opponent}"
)
DEFAULT_YT_DESCRIPTION_TEMPLATE = (
    "{date}. {tournament_full}. Match {match_index}. {opponent}"
)

# Default render-output naming. Replicates the previous hard-coded
# behavior exactly so existing files keep their format unless the team
# overrides these on the profile. Documented in app/render/naming.py.
DEFAULT_FULL_RENDER_TEMPLATE = "{label}_{timestamp}.mp4"
DEFAULT_HIGHLIGHT_TEMPLATE = (
    "highlights/{team}/{player}/{action}/"
    "{date}_vs_{opponent}_{match_timestamp}_{action}.mp4"
)
DEFAULT_FOCUSED_TEMPLATE = (
    "focused/{team}/{player}/{action}/"
    "{date}_vs_{opponent}_{start_timestamp}-{end_timestamp}.mp4"
)


@dataclass
class NamingConfig:
    """Per-team render-output naming templates.

    Stored under `roster.json -> naming`. Defaults reproduce the
    previous hard-coded behavior exactly so a team with no `naming`
    block (or any subset of fields unset) still gets the same paths
    as before this feature was added.

    The three templates apply to different render kinds; the variables
    available to each differ - see `app/render/naming.py` for the
    full list.
    """

    full_render_template: str = DEFAULT_FULL_RENDER_TEMPLATE
    highlight_template: str = DEFAULT_HIGHLIGHT_TEMPLATE
    focused_template: str = DEFAULT_FOCUSED_TEMPLATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "full_render_template": self.full_render_template,
            "highlight_template": self.highlight_template,
            "focused_template": self.focused_template,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "NamingConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            full_render_template=(
                data.get("full_render_template") or DEFAULT_FULL_RENDER_TEMPLATE
            ),
            highlight_template=(
                data.get("highlight_template") or DEFAULT_HIGHLIGHT_TEMPLATE
            ),
            focused_template=(
                data.get("focused_template") or DEFAULT_FOCUSED_TEMPLATE
            ),
        )


@dataclass
class YouTubeConfig:
    """Per-team YouTube upload defaults.

    Stored under `roster.json -> youtube`. All fields are optional so an
    older roster.json (no `youtube` block) still loads cleanly - the
    upload UI just treats the team as "not YouTube-configured" and
    disables the upload button with a tooltip.
    """

    # YouTube `privacyStatus`. Defaults to "unlisted" because public is
    # rarely what you want for a fresh upload and private would prevent
    # sharing via link.
    privacy_status: str = "unlisted"
    # Playlist to attach uploaded videos to. None = don't add to any
    # playlist. We don't validate the id format here; the YouTube API
    # will reject malformed ids at upload time.
    playlist_id: Optional[str] = None
    # Templates run through `str.format(**vars)` at upload-enqueue time.
    title_template: str = DEFAULT_YT_TITLE_TEMPLATE
    description_template: str = DEFAULT_YT_DESCRIPTION_TEMPLATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "privacy_status": self.privacy_status,
            "playlist_id": self.playlist_id,
            "title_template": self.title_template,
            "description_template": self.description_template,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "YouTubeConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            privacy_status=data.get("privacy_status") or "unlisted",
            playlist_id=data.get("playlist_id") or None,
            title_template=(
                data.get("title_template") or DEFAULT_YT_TITLE_TEMPLATE
            ),
            description_template=(
                data.get("description_template")
                or DEFAULT_YT_DESCRIPTION_TEMPLATE
            ),
        )


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
    # Team-level YouTube upload defaults. Always present (defaults applied
    # when the roster.json has no `youtube` block) so call sites can read
    # `roster.youtube.privacy_status` etc. without None-checking.
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    # Output-file naming templates for renders. Like `youtube`, always
    # present with defaults applied - call sites can read
    # `roster.naming.full_render_template` without None-checking.
    naming: NamingConfig = field(default_factory=NamingConfig)

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

    def to_dict(self, *, include_admin: bool = True) -> dict[str, Any]:
        """Serialize the roster.

        `include_admin` controls whether the team-owner-only blocks
        (`youtube`, `naming`) are emitted. They're meaningful for the
        team's OWN roster.json - that's where you configure where YOUR
        renders get uploaded and how YOUR output files are named - but
        carry no meaning for the opponent_roster embedded in a match
        (the opponent isn't going to upload anything to your channel
        nor influence your output paths). `Match.to_dict` passes False
        so old match.json files stop accumulating `"youtube": {...}`
        / `"naming": {...}` junk under `opponent_roster`.

        Loading is always lenient: `from_dict` accepts either flavor,
        so a freshly-saved match.json (no admin fields) and a legacy
        one (admin fields present from the bug era) both load cleanly.
        """
        out: dict[str, Any] = {
            "team_name": self.team_name,
            "team_color": self.team_color,
        }
        if include_admin:
            out["youtube"] = self.youtube.to_dict()
            out["naming"] = self.naming.to_dict()
        out["players"] = [p.to_dict() for p in self.players]
        return out

    @classmethod
    def from_dict(cls, data: Any) -> "Roster":
        if isinstance(data, list):
            return cls(players=[Player.from_dict(p) for p in data])
        if isinstance(data, dict):
            return cls(
                team_name=data.get("team_name"),
                team_color=data.get("team_color"),
                youtube=YouTubeConfig.from_dict(data.get("youtube")),
                naming=NamingConfig.from_dict(data.get("naming")),
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
