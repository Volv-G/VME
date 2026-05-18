"""Render filename / path templating.

Three templates per team (configured on the team profile, defaults
preserve the legacy hard-coded behavior):

  full_render_template      filename inside <match>/renders/
                            Default: "{label}_{timestamp}.mp4"
                            Used by full / preview renders.

  highlight_template        path inside <match>/renders/ (slashes OK)
                            Default: "highlights/{team}/{player}/{action}/
                                      {date}_vs_{opponent}_{match_timestamp}_{action}.mp4"
                            Used by `highlights_from_match`.

  focused_template          path inside <match>/renders/ (slashes OK)
                            Default: "focused/{team}/{player}/{date}_vs_{opponent}_
                                      {start_timestamp}-{end_timestamp}.mp4"
                            Used by `focused_from_match`.

Variable values are filesystem-sanitized via `paths.safe_segment` before
being substituted, so the user can put unsafe characters in opponent /
team names without breaking the path. Slashes inside the TEMPLATE are
preserved (they create subfolders); slashes inside a VARIABLE value
would be replaced by `_` by `safe_segment`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..domain.match import Match
from ..domain.roster import Roster
from ..domain.team import Team
from ..library import paths


# ---------------------------------------------------------------------------
# Default templates - kept here (not on the NamingConfig dataclass) so a
# single canonical string is referenced from both the domain code and
# the frontend's placeholder text via the API.
# ---------------------------------------------------------------------------

DEFAULT_FULL_RENDER_TEMPLATE = "{label}_{timestamp}.mp4"
DEFAULT_HIGHLIGHT_TEMPLATE = (
    "highlights/{team}/{player}/{action}/"
    "{date}_vs_{opponent}_{match_timestamp}_{action}.mp4"
)
DEFAULT_FOCUSED_TEMPLATE = (
    "focused/{team}/{player}/{action}/"
    "{date}_vs_{opponent}_{start_timestamp}-{end_timestamp}.mp4"
)


# ---------------------------------------------------------------------------
# Value formatters
# ---------------------------------------------------------------------------


def _label_segment(label: str) -> str:
    """Mirror the legacy full-render label transform: lowercase, spaces
    to underscores. Distinct from `safe_segment` (which is more
    aggressive about non-alphanumerics) - used only for `{label}` so the
    legacy default produces filenames identical to before."""
    return (label or "render").lower().replace(" ", "_")


def _wallclock_timestamp() -> str:
    """`YYYYMMDD_HHMMSS` - matches the legacy hard-coded format."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def format_match_timestamp(global_frame: int, fps: float) -> str:
    """`HHhMMmSSs` of an in-match frame position. Zero-padded always so
    files sort chronologically inside a folder."""
    total_s = int(global_frame / max(fps, 1e-6))
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}h{m:02d}m{s:02d}s"


def _slug_to_display(slug: str) -> str:
    """Pretty-print a folder slug: replace underscores with spaces."""
    return (slug or "").replace("_", " ").strip() or slug


# ---------------------------------------------------------------------------
# Variable builders
# ---------------------------------------------------------------------------


def _common_match_vars(
    *,
    team: str,
    tournament: str,
    date: str,
    match: str,
    match_obj: Match,
    home_team_name: str,
    tournament_abbreviation: str,
    tournament_full_name: str,
) -> dict[str, str]:
    """Variables available to ALL three templates.

    Each value is filesystem-sanitized so it's safe to drop into a path
    segment. Display-only tokens (tournament_abbr / tournament_full) are
    kept human-readable since they're typically used in full renders'
    suffix metadata rather than as folder names.
    """
    idx, _ = paths.parse_match_folder(match)
    return {
        "date": paths.safe_segment(date) if date else "no-date",
        "opponent": (
            paths.safe_segment(match_obj.opponent)
            if match_obj.opponent
            else "TBD"
        ),
        "team": paths.safe_segment(home_team_name) if home_team_name else "Home",
        "tournament": paths.safe_segment(tournament) if tournament else "tournament",
        # Display variants for cases where the user wants the long name
        # in the filename. Sanitized too - even the long name will end
        # up in a path.
        "tournament_abbr": paths.safe_segment(
            tournament_abbreviation or _slug_to_display(tournament)
        ),
        "tournament_full": paths.safe_segment(
            tournament_full_name
            or tournament_abbreviation
            or _slug_to_display(tournament)
        ),
        "match_index": str(idx) if idx is not None else "0",
    }


def build_full_render_vars(
    *,
    team: str,
    tournament: str,
    date: str,
    match: str,
    match_obj: Match,
    home_team_name: str,
    tournament_abbreviation: str = "",
    tournament_full_name: str = "",
    label: str = "render",
    timestamp: Optional[str] = None,
) -> dict[str, str]:
    """Variables for the full / preview render filename template."""
    vars_ = _common_match_vars(
        team=team,
        tournament=tournament,
        date=date,
        match=match,
        match_obj=match_obj,
        home_team_name=home_team_name,
        tournament_abbreviation=tournament_abbreviation,
        tournament_full_name=tournament_full_name,
    )
    vars_["label"] = _label_segment(label)
    vars_["timestamp"] = timestamp or _wallclock_timestamp()
    return vars_


def _player_vars(
    *,
    team_enum: Team,
    jersey: int,
    home_roster: Roster,
    home_team_name: str,
    match_obj: Match,
) -> dict[str, str]:
    """Player + team folder/name fragments shared by highlight + focused
    templates. Mirrors the legacy `_player_and_team_folders` helper but
    exposes the pieces individually so users can rearrange them."""
    if team_enum == Team.HOME:
        roster = home_roster
        team_label = home_team_name or "Home"
    else:
        roster = match_obj.opponent_roster
        team_label = (
            match_obj.opponent_roster.team_name
            or match_obj.opponent
            or "Opponent"
        )
    player = roster.find_by_number(jersey)
    name = player.short_name or player.name if player else None
    return {
        # `{team}` is overridden here with the SIDE the player belongs to
        # (home vs. opponent), which is what users almost always want
        # for highlights. For full renders `{team}` falls back to the
        # home team (see `build_full_render_vars`).
        "team": paths.safe_segment(team_label),
        "player": paths.player_folder_name(jersey, name),
        "player_number": f"{int(jersey):02d}",
        "player_name": paths.safe_segment(name) if name else "",
    }


def build_highlight_vars(
    *,
    team: str,
    tournament: str,
    date: str,
    match: str,
    match_obj: Match,
    home_team_name: str,
    tournament_abbreviation: str,
    tournament_full_name: str,
    team_enum: Team,
    jersey: int,
    home_roster: Roster,
    action: str,
    match_timestamp: str,
) -> dict[str, str]:
    """Variables for one highlight clip's path template."""
    vars_ = _common_match_vars(
        team=team,
        tournament=tournament,
        date=date,
        match=match,
        match_obj=match_obj,
        home_team_name=home_team_name,
        tournament_abbreviation=tournament_abbreviation,
        tournament_full_name=tournament_full_name,
    )
    vars_.update(
        _player_vars(
            team_enum=team_enum,
            jersey=jersey,
            home_roster=home_roster,
            home_team_name=home_team_name,
            match_obj=match_obj,
        )
    )
    vars_["action"] = paths.safe_segment(action) if action else "highlight"
    vars_["match_timestamp"] = match_timestamp
    return vars_


def build_focused_vars(
    *,
    team: str,
    tournament: str,
    date: str,
    match: str,
    match_obj: Match,
    home_team_name: str,
    tournament_abbreviation: str,
    tournament_full_name: str,
    team_enum: Team,
    jersey: int,
    home_roster: Roster,
    start_timestamp: str,
    end_timestamp: str,
    action: str = "focus",
) -> dict[str, str]:
    """Variables for one focused clip's path template.

    `action` is the type_name of the first PlayerEvent by the focused
    player inside the same rally (computed by the caller via
    `batch._first_action_for_player_in_rally`). Defaults to "focus"
    when the rally contains no player-tagged action - keeps the
    template valid on lightly-annotated matches.
    """
    vars_ = _common_match_vars(
        team=team,
        tournament=tournament,
        date=date,
        match=match,
        match_obj=match_obj,
        home_team_name=home_team_name,
        tournament_abbreviation=tournament_abbreviation,
        tournament_full_name=tournament_full_name,
    )
    vars_.update(
        _player_vars(
            team_enum=team_enum,
            jersey=jersey,
            home_roster=home_roster,
            home_team_name=home_team_name,
            match_obj=match_obj,
        )
    )
    vars_["start_timestamp"] = start_timestamp
    vars_["end_timestamp"] = end_timestamp
    vars_["action"] = paths.safe_segment(action) if action else "focus"
    return vars_


# ---------------------------------------------------------------------------
# Rendering + safety
# ---------------------------------------------------------------------------


class TemplateError(ValueError):
    """Raised when a template references an unknown placeholder or
    produces a path that tries to escape the renders root."""


def render_naming_template(template: str, vars_: dict[str, str]) -> str:
    """Apply `str.format(**vars_)` and reject path-escape attempts.

    The template TEXT is trusted (the user wrote it), but we still
    refuse `..` path segments in the OUTPUT - those would let a bad
    template traverse out of the renders dir. Variable values are
    already sanitized so a `..` in the output can only originate from
    the literal template text.

    Always normalizes to forward slashes so callers can rely on a
    POSIX-style relative path regardless of the OS where it was
    rendered. The caller composes the final disk path with
    `Path(renders_root) / result` which converts to native separators.
    """
    try:
        out = template.format(**vars_)
    except KeyError as exc:
        # Unknown placeholder. Re-raise with a friendlier message so
        # the API can return a clear 400.
        raise TemplateError(
            f"Unknown placeholder in template: {exc.args[0]!r}. "
            f"Available: {sorted(vars_.keys())}"
        ) from exc
    except (IndexError, ValueError) as exc:
        raise TemplateError(f"Failed to format template: {exc}") from exc

    # Normalize separators and reject absolute / traversing paths.
    norm = out.replace("\\", "/")
    if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
        raise TemplateError(
            f"Template produced an absolute path: {norm!r}"
        )
    if any(part in ("", "..") for part in norm.split("/") if part not in (".",)):
        # Empty path segments come from things like `foo//bar` (a typo
        # in the template); `..` is the security-relevant case. Either
        # way the output is malformed and we refuse to render it.
        raise TemplateError(
            f"Template produced an invalid path: {norm!r}"
        )
    return norm
