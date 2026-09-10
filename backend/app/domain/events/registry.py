"""Event class registry for (de)serialization by `type` string."""

from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass
from typing import Any, Type

from .base import MatchEvent


EVENT_REGISTRY: dict[str, Type[MatchEvent]] = {}


def register_event(cls: Type[MatchEvent]) -> Type[MatchEvent]:
    """Class decorator: register a MatchEvent subclass by its `type_name`."""
    type_name = getattr(cls, "type_name", None)
    if not type_name or type_name == "base":
        raise ValueError(f"Event class {cls.__name__} must define `type_name`")
    if type_name in EVENT_REGISTRY:
        raise ValueError(f"Duplicate event type_name: {type_name}")
    EVENT_REGISTRY[type_name] = cls
    return cls


# Identity fields are always emitted, even when they happen to equal the
# dataclass default - they're load-bearing for round-tripping and useful
# at a glance when reading the JSON.
#
# `position` is here for a different reason: it is the SUBJECT of the
# event it belongs to (a substitution is "who came in, and where"), and
# omitting it forces every reader to know that the default is 1. The UI
# didn't, and rendered a sub into position 1 as "enters at P0" - and
# then looked up the outgoing player at that non-existent position, so
# it couldn't name them either. A value the reader must guess is not a
# default worth saving three bytes on.
_ALWAYS_EMIT = frozenset({"id", "clip_id", "local_frame", "type", "position"})


def event_to_dict(event: MatchEvent) -> dict[str, Any]:
    """Serialize an event to a JSON-compatible dict.

    Includes `type` (from `type_name`), `id`, and any dataclass fields except
    transient ones (`state`). Fields whose value equals the dataclass default
    are omitted to keep match.json readable - the deserializer will recreate
    them from the same defaults. Identity fields (`id`, `clip_id`,
    `local_frame`) are kept regardless so each event row stays self-describing.
    """
    data: dict[str, Any] = {"type": event.type_name, "id": event.id}
    if is_dataclass(event):
        for f in fields(event):
            if f.name in ("state",):
                continue
            value = getattr(event, f.name)
            if hasattr(value, "value"):  # Enum
                value = value.value
            # Skip fields that match their declared default. `default_factory`
            # gets resolved when MISSING isn't set; we compare against the
            # actual default value. Identity fields bypass this filter.
            if f.name not in _ALWAYS_EMIT and _is_default(f, value):
                continue
            data[f.name] = value
    return data


def _is_default(f: Any, value: Any) -> bool:
    """True iff `value` equals the dataclass field's declared default."""
    if f.default is not MISSING:
        # Tuples and lists compare structurally, which is what we want for
        # things like `blend_color = (0, 0, 0)`.
        return value == f.default
    if f.default_factory is not MISSING:  # type: ignore[misc]
        try:
            return value == f.default_factory()  # type: ignore[misc]
        except Exception:
            return False
    return False


def event_from_dict(data: dict[str, Any]) -> MatchEvent:
    """Deserialize an event from a dict produced by `event_to_dict`."""
    type_name = data.get("type")
    if not type_name or type_name not in EVENT_REGISTRY:
        raise ValueError(f"Unknown event type: {type_name!r}")
    cls = EVENT_REGISTRY[type_name]

    kwargs: dict[str, Any] = {}
    if is_dataclass(cls):
        for f in fields(cls):
            if f.name in ("state",):
                continue
            if f.name in data:
                kwargs[f.name] = _coerce(f.type, data[f.name])
    return cls(**kwargs)


def _coerce(field_type: Any, value: Any) -> Any:
    """Best-effort coercion of dict values to dataclass field types.

    Handles Team (str enum) and tuples; defers anything else to the dataclass
    constructor.
    """
    from ..team import Team

    if value is None:
        return None
    type_str = field_type if isinstance(field_type, str) else getattr(field_type, "__name__", "")
    if "Team" in type_str and isinstance(value, str):
        return Team(value)
    if "tuple" in type_str and isinstance(value, list):
        return tuple(value)
    return value
