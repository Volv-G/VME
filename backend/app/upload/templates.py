"""Format title / description templates for YouTube uploads.

Variables (all strings, never None) made available to `str.format(...)`:

  {date}              - the match date in `YYYY.MM.DD` form (e.g.
                        "2026.05.17"). Folders live on disk as ISO
                        (`YYYY-MM-DD`) but the rendered title looks
                        cleaner with dots; the conversion happens in
                        `paths.format_date_for_template`.
  {team}              - home team display name
  {opponent}          - opponent display name
  {tournament_abbr}   - short tournament name (sidecar or folder fallback)
  {tournament_full}   - long tournament name (sidecar or abbrev fallback)
  {match_index}       - 1-based match order on the date (e.g. "3" - bare,
                        wrap with `M{match_index}` or `Match {match_index}`
                        yourself in the template; user said never more
                        than 9 matches/day so padding is unnecessary)

Player-reel uploads additionally get (see `with_reel`):

  {player}            - "#8 Kate G" (number + name, as displayed)
  {player_number}     - "8"
  {player_name}       - "Kate G"
  {clip_count}        - number of plays in the reel ("5"), derived from
                        the chapter sidecar
  {chapters}          - the timestamp/chapter block. When a description
                        template omits it, the upload job appends the
                        block at the end instead, so chapters are never
                        silently lost.

These are empty strings for non-reel uploads rather than missing, so a
team can share one template between both if they want to.

Missing variables in user templates raise a KeyError that bubbles up to
the upload endpoint; that's the right behavior - we want a 400 on a
broken template, not a silent upload with literal `{foo}` in the title.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from ..domain.match import Match
from ..domain.roster import Roster
from ..domain.tournament import Tournament
from ..library import paths


@dataclass
class TemplateVars:
    """Resolved string values for the template placeholders."""

    date: str
    team: str
    opponent: str
    tournament_abbr: str
    tournament_full: str
    match_index: str
    # Reel-only extras. Empty strings for match uploads so a template
    # that mentions them still formats (it just renders blanks) instead
    # of raising KeyError.
    player: str = ""
    player_number: str = ""
    player_name: str = ""
    clip_count: str = ""
    chapters: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "date": self.date,
            "team": self.team,
            "opponent": self.opponent,
            "tournament_abbr": self.tournament_abbr,
            "tournament_full": self.tournament_full,
            "match_index": self.match_index,
            "player": self.player,
            "player_number": self.player_number,
            "player_name": self.player_name,
            "clip_count": self.clip_count,
            "chapters": self.chapters,
        }

    def with_reel(
        self,
        *,
        jersey: Optional[int],
        player_name: str,
        clip_count: int,
        chapters: str,
    ) -> "TemplateVars":
        """Copy with the player-reel placeholders filled in."""
        player = ""
        if jersey is not None:
            player = f"#{jersey}" + (f" {player_name}" if player_name else "")
        elif player_name:
            player = player_name
        return replace(
            self,
            player=player,
            player_number=(str(jersey) if jersey is not None else ""),
            player_name=player_name,
            clip_count=str(clip_count) if clip_count else "",
            chapters=chapters,
        )


def _slug_to_display(slug: str) -> str:
    """Pretty-print a folder slug: replace underscores with spaces."""
    return (slug or "").replace("_", " ").strip() or slug


def build_vars(
    *,
    team: str,
    tournament: str,
    date: str,
    match: str,
    match_obj: Match,
    roster: Roster,
    tournament_info: Tournament,
) -> TemplateVars:
    """Resolve all template variables for a single match upload.

    Folder slugs are used as fallbacks for any unset display strings -
    pretty-printed (`_` -> space) so the literal slug never appears in
    titles. The match folder's NN prefix is parsed for `match_index`;
    legacy unprefixed folders fall back to "?".
    """
    idx, _ = paths.parse_match_folder(match)
    tournament_slug_pretty = _slug_to_display(tournament)
    abbr = tournament_info.resolve_abbreviation(tournament_slug_pretty)
    full = tournament_info.resolve_full_name(tournament_slug_pretty)
    return TemplateVars(
        date=paths.format_date_for_template(date),
        team=(roster.team_name or _slug_to_display(team)),
        opponent=(match_obj.opponent or _slug_to_display(match)),
        tournament_abbr=abbr,
        tournament_full=full,
        match_index=(str(idx) if idx is not None else "?"),
    )


CHAPTERS_PLACEHOLDER = "{chapters}"


def template_uses_chapters(template: str) -> bool:
    """Whether a description template positions the chapter block itself.

    When it doesn't, the upload job appends the block after the rendered
    description - which is what the reel feature did before these
    templates existed, and keeps chapters working for teams that write
    their own description without thinking about them.
    """
    return CHAPTERS_PLACEHOLDER in (template or "")


def render_template(template: str, vars_: TemplateVars) -> str:
    """Apply `str.format` to a user-supplied template.

    Lets a bare `{` slip through by treating `{{`/`}}` as escapes (the
    standard format behavior). KeyError on unknown placeholders is
    intentional - it should bubble up so the API returns a 400 instead
    of uploading a video with broken metadata.
    """
    return template.format(**vars_.as_dict())
