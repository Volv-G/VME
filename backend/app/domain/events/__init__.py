"""Event class hierarchy.

Importing this package registers all concrete event classes with the registry
so they can be (de)serialized by `type` string.
"""

from .base import MatchEvent, ValidationError  # noqa: F401
from .registry import (  # noqa: F401
    EVENT_REGISTRY,
    event_from_dict,
    event_to_dict,
    register_event,
)

# Importing the modules below registers their event classes.
from . import timeline  # noqa: F401
from . import lifecycle  # noqa: F401
from . import serve  # noqa: F401
from . import scoring  # noqa: F401
from . import player as player_events  # noqa: F401
from . import roster as roster_events  # noqa: F401
from . import message  # noqa: F401
from . import focus  # noqa: F401
