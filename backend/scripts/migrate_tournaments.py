"""Move legacy tournament folders under `<team>/tournaments/`.

Before: every directory under a team folder was assumed to be a
tournament, so `players/` (the photo folder) appeared in the tournament
list with "0 matches". Tournaments now live in their own subfolder;
the app still READS the old layout, so this migration is optional and
can be run whenever.

The move is a rename within the same volume - instant, no copying, even
for a terabyte of match footage. It is also the reason to be careful:
a rename fails if anything holds the folder open (an Explorer window, a
terminal sitting in it, a running render), so run it with the app idle.

Usage:
    python -m scripts.migrate_tournaments            # dry run, all teams
    python -m scripts.migrate_tournaments --apply
    python -m scripts.migrate_tournaments --apply --team Eastlake_JV

Run from the `backend` directory with the project venv, so that
VME_MEDIA_ROOT resolves the same way the service resolves it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import MEDIA_ROOT  # noqa: E402
from app.library import paths  # noqa: E402


def legacy_dirs(team: str) -> list[Path]:
    """Tournament folders still sitting directly under the team folder."""
    base = paths.team_dir(team)
    if not base.is_dir():
        return []
    out = []
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name in paths.RESERVED_TEAM_SUBDIRS:
            continue
        out.append(d)
    return out


def migrate_team(team: str, *, apply: bool) -> tuple[int, int]:
    """Move one team's legacy folders. Returns (moved, skipped)."""
    root = paths.tournaments_root(team)
    moved = skipped = 0
    for src in legacy_dirs(team):
        dst = root / src.name
        if dst.exists():
            print(f"  SKIP  {src.name}: already exists under tournaments/")
            skipped += 1
            continue
        if not apply:
            print(f"  would move  {src.name}  ->  tournaments/{src.name}")
            moved += 1
            continue
        root.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(src, dst)
        except OSError as exc:
            # Almost always WinError 5: something holds the directory
            # open. Report which one and keep going - a partial
            # migration is fine, the app reads both layouts.
            print(f"  FAIL  {src.name}: {exc}")
            skipped += 1
            continue
        print(f"  moved  {src.name}  ->  tournaments/{src.name}")
        moved += 1
    return moved, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually move")
    ap.add_argument("--team", help="only this team (folder name)")
    args = ap.parse_args()

    print(f"media root: {MEDIA_ROOT}")
    if not MEDIA_ROOT.is_dir():
        print("media root does not exist")
        return 1

    teams = (
        [args.team]
        if args.team
        else [d.name for d in sorted(MEDIA_ROOT.iterdir()) if d.is_dir()]
    )
    total_moved = total_skipped = 0
    for team in teams:
        pending = legacy_dirs(team)
        if not pending:
            continue
        print(f"\n{team}:")
        moved, skipped = migrate_team(team, apply=args.apply)
        total_moved += moved
        total_skipped += skipped

    if not total_moved and not total_skipped:
        print("\nNothing to migrate.")
    elif args.apply:
        print(f"\nDone - {total_moved} moved, {total_skipped} skipped.")
    else:
        print(
            f"\nDry run - {total_moved} would move, {total_skipped} skipped. "
            "Re-run with --apply."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
