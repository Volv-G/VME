"""Free-form annotation messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Optional

from ..effects import MessagePopupEffect, OverlayEffect
from .base import MatchEvent
from .registry import register_event


@register_event
@dataclass
class MessageEvent(MatchEvent):
    """A freeform popup message displayed on the rendered video."""

    type_name: ClassVar[str] = "message"

    text: str = ""
    image_path: Optional[str] = None

    @property
    def overlay_effect(self) -> Optional[OverlayEffect]:
        return MessagePopupEffect(text=self.text, image_path=self.image_path)
