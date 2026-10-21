"""Pygame UI subsystem: assets, theme, fonts, widgets."""

from .assets import AssetManager
from .fonts import FontRegistry
from .theme import Theme, default_theme
from .input import InputRouter, InputState
from .motion import MotionKind, MotionPolicy
from .visual import VisualMode, VisualPolicy, VisualTheme
from .widgets.sound_caption import SoundCaptionOverlay

__all__ = [
    "AssetManager",
    "FontRegistry",
    "Theme",
    "default_theme",
    "InputRouter",
    "InputState",
    "MotionKind",
    "MotionPolicy",
    "VisualMode",
    "VisualPolicy",
    "VisualTheme",
    "SoundCaptionOverlay",
]
