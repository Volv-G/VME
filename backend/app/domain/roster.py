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
# Player-reel uploads get their own title/description because the
# match-level ones would give all twelve reels from a match the same
# name. Defaults reproduce exactly what the hardcoded reel path used to
# produce ('#8 Kate G - <match title>' + description + chapter list), so
# a team that never touches these sees no change.
# Extra placeholders available here: {player}, {player_number},
# {player_name}, {clip_count} and {chapters}.
# Date first, then player: YouTube's own listings (and any file listing)
# sort alphabetically, so leading with the date keeps a match's reels
# grouped together and in chronological order across matches.
DEFAULT_YT_REEL_TITLE_TEMPLATE = (
    "{date} - {player} - {tournament_abbr}. M{match_index}. {opponent}"
)
DEFAULT_YT_REEL_DESCRIPTION_TEMPLATE = (
    "{date}. {tournament_full}. Match {match_index}. {opponent}\n\n{chapters}"
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
# Player reels: ONE file per player holding all of their plays, meant for
# sharing/uploading (see app/render/reels.py). Flat per-player folder -
# there's exactly one reel per player per match, so no action subfolder.
DEFAULT_REEL_TEMPLATE = (
    "reels/{team}/{player}/{date}_vs_{opponent}_{player}_reel.mp4"
)

# Media-server (Jellyfin/Plex/...) publishing. The renders tree is
# organised for editing - `<tournament>/<date>/<NN_Opponent>/renders/
# full_<timestamp>.mp4` - which tells a media server nothing. Copies go
# out under one flat, self-describing name per match instead, with the
# Jellyfin sidecar images renamed to match.
DEFAULT_MEDIA_SERVER_TEMPLATE = "{date}. {tournament_abbr}. M{match_index}. {opponent}"


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
    reel_template: str = DEFAULT_REEL_TEMPLATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "full_render_template": self.full_render_template,
            "highlight_template": self.highlight_template,
            "focused_template": self.focused_template,
            "reel_template": self.reel_template,
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
            reel_template=(data.get("reel_template") or DEFAULT_REEL_TEMPLATE),
        )


@dataclass
class MediaServerConfig:
    """Where finished renders get copied for a media server to play.

    Stored under `roster.json -> media_server`. `path` is a plain
    filesystem location on the SERVER (a local folder or a UNC share it
    can reach) - the copy is done by the backend, not the browser, so a
    multi-gigabyte match never travels through the UI.

    Empty `path` = the feature is off, and the UI hides the copy button.
    """

    path: Optional[str] = None
    # Base name (no extension) for the copied files. Same placeholders
    # as the YouTube templates - see `app/upload/templates.py`.
    filename_template: str = DEFAULT_MEDIA_SERVER_TEMPLATE

    @property
    def enabled(self) -> bool:
        return bool((self.path or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "filename_template": self.filename_template,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MediaServerConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            path=(data.get("path") or None),
            filename_template=(
                data.get("filename_template") or DEFAULT_MEDIA_SERVER_TEMPLATE
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
    # Same, but for player-reel uploads (one video per player).
    reel_title_template: str = DEFAULT_YT_REEL_TITLE_TEMPLATE
    reel_description_template: str = DEFAULT_YT_REEL_DESCRIPTION_TEMPLATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "privacy_status": self.privacy_status,
            "playlist_id": self.playlist_id,
            "title_template": self.title_template,
            "description_template": self.description_template,
            "reel_title_template": self.reel_title_template,
            "reel_description_template": self.reel_description_template,
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
            reel_title_template=(
                data.get("reel_title_template")
                or DEFAULT_YT_REEL_TITLE_TEMPLATE
            ),
            reel_description_template=(
                data.get("reel_description_template")
                or DEFAULT_YT_REEL_DESCRIPTION_TEMPLATE
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
    # Team logo filename, RELATIVE to the roster's own directory: the
    # team folder for a team roster, the match folder for the opponent
    # roster embedded in match.json. Relative so the media tree stays
    # movable (see `paths.team_logo_file` / `paths.opponent_logo_file`).
    # None = no logo uploaded.
    team_logo_path: Optional[str] = None
    # Team-level YouTube upload defaults. Always present (defaults applied
    # when the roster.json has no `youtube` block) so call sites can read
    # `roster.youtube.privacy_status` etc. without None-checking.
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    # Output-file naming templates for renders. Like `youtube`, always
    # present with defaults applied - call sites can read
    # `roster.naming.full_render_template` without None-checking.
    naming: NamingConfig = field(default_factory=NamingConfig)
    # Where to copy finished renders for a media server (Jellyfin etc.).
    # Always present; `enabled` is False until a path is configured.
    media_server: MediaServerConfig = field(default_factory=MediaServerConfig)

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
            "team_logo_path": self.team_logo_path,
        }
        if include_admin:
            out["youtube"] = self.youtube.to_dict()
            out["naming"] = self.naming.to_dict()
            out["media_server"] = self.media_server.to_dict()
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
                team_logo_path=data.get("team_logo_path"),
                youtube=YouTubeConfig.from_dict(data.get("youtube")),
                naming=NamingConfig.from_dict(data.get("naming")),
                media_server=MediaServerConfig.from_dict(data.get("media_server")),
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
