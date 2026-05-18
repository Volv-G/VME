"""Tournament: per-tournament sidecar metadata.

Lives at `<Team>/<Tournament>/tournament.json` (sibling of the per-date
subfolders). Currently just stores display names: an `abbreviation`
(used in title templates / compact UI) and a `full_name` (used in
description templates / verbose UI). Both are optional; missing values
fall back to the folder slug with `_` replaced by spaces.

Future fields (e.g. per-tournament YouTube playlist override) plug in
here without needing a schema-version bump - JSON keeps unknown keys
on round-trip via the explicit field list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class Tournament:
    """Display metadata for a tournament folder."""

    # Short form, e.g. "Spring 25". Used in title templates and any
    # context where vertical/horizontal space is tight.
    abbreviation: Optional[str] = None
    # Long form, e.g. "Spring League 2025 - Division B". Used in
    # descriptions and longer text contexts.
    full_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "abbreviation": self.abbreviation,
            "full_name": self.full_name,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Tournament":
        if not isinstance(data, dict):
            return cls()
        return cls(
            abbreviation=data.get("abbreviation") or None,
            full_name=data.get("full_name") or None,
        )

    @classmethod
    def load(cls, path: str | Path) -> "Tournament":
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except (OSError, ValueError):
            return cls()

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    def resolve_abbreviation(self, fallback: str) -> str:
        """Return `abbreviation` if set, else `fallback` (typically the
        folder slug pretty-printed with `_` -> space)."""
        return self.abbreviation or fallback

    def resolve_full_name(self, fallback: str) -> str:
        """Return `full_name` if set, else `abbreviation` if set, else
        `fallback`. The chain matches user intent: a full name is more
        specific than an abbreviation, which is more specific than the
        folder slug."""
        return self.full_name or self.abbreviation or fallback
