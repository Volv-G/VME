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

Missing variables in user templates raise a KeyError that bubbles up to
the upload endpoint; that's the right behavior - we want a 400 on a
broken template, not a silent upload with literal `{foo}` in the title.
"""

from __future__ import annotations

from dataclasses import dataclass

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

    def as_dict(self) -> dict[str, str]:
        return {
            "date": self.date,
            "team": self.team,
            "opponent": self.opponent,
            "tournament_abbr": self.tournament_abbr,
            "tournament_full": self.tournament_full,
            "match_index": self.match_index,
        }


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


def render_template(template: str, vars_: TemplateVars) -> str:
    """Apply `str.format` to a user-supplied template.

    Lets a bare `{` slip through by treating `{{`/`}}` as escapes (the
    standard format behavior). KeyError on unknown placeholders is
    intentional - it should bubble up so the API returns a 400 instead
    of uploading a video with broken metadata.
    """
    return template.format(**vars_.as_dict())
