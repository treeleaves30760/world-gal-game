"""Core engine subsystems."""

from .game_state import GameState
from .affection import AffectionTracker
from .event_log import EventLog
from .story_graph import (
    ChoiceCardDetail,
    ChoiceCardSpec,
    SoundCaptionSpec,
    StoryGraph,
    StoryNode,
    authored_story_audio_refs,
    rebind_authored_audio_meta,
    rebind_authored_dialogue_history_voices,
    rebind_authored_choice_presentations,
    rebind_authored_story_catalog,
)
from .map_system import MapSystem, Location, rebind_authored_map_catalog
from .spatial_map import (
    EdgeDestination,
    EdgeDestinationSpec,
    SpatialMapNodeSpec,
    SpatialMapPresentation,
    SpatialMapSpec,
    parse_spatial_map_spec,
    spatial_map_from_meta,
)
from .title_screen_spec import TitleScreenSpec, parse_title_screen_spec
from .ui_skin_spec import UISkinSpec, parse_ui_skin_spec
from .cg_gallery_spec import CGGalleryEntrySpec, CGGalleryManifest
from .music_room_spec import (
    MusicRoomEntrySpec,
    MusicRoomManifest,
    music_room_localization_id,
)
from .media_authority import (
    MediaAuthority,
    build_media_authority,
    canonical_pack_media_id,
    filter_authorized_cg_ids,
    filter_authorized_music_ids,
    rebind_authorized_media_progress,
)
from .endings import EndingTracker, rebind_authored_ending_catalog
from .time_system import TimeSystem, TimeOfDay, DayOfWeek
from .save_manager import SaveManager

__all__ = [
    "GameState",
    "AffectionTracker",
    "EventLog",
    "StoryGraph",
    "StoryNode",
    "ChoiceCardDetail",
    "ChoiceCardSpec",
    "SoundCaptionSpec",
    "authored_story_audio_refs",
    "rebind_authored_audio_meta",
    "rebind_authored_dialogue_history_voices",
    "rebind_authored_choice_presentations",
    "rebind_authored_story_catalog",
    "MapSystem",
    "Location",
    "rebind_authored_map_catalog",
    "EdgeDestination",
    "EdgeDestinationSpec",
    "SpatialMapNodeSpec",
    "SpatialMapPresentation",
    "SpatialMapSpec",
    "parse_spatial_map_spec",
    "spatial_map_from_meta",
    "TitleScreenSpec",
    "parse_title_screen_spec",
    "UISkinSpec",
    "parse_ui_skin_spec",
    "CGGalleryEntrySpec",
    "CGGalleryManifest",
    "MusicRoomEntrySpec",
    "MusicRoomManifest",
    "music_room_localization_id",
    "MediaAuthority",
    "build_media_authority",
    "canonical_pack_media_id",
    "filter_authorized_cg_ids",
    "filter_authorized_music_ids",
    "rebind_authorized_media_progress",
    "EndingTracker",
    "rebind_authored_ending_catalog",
    "TimeSystem",
    "TimeOfDay",
    "DayOfWeek",
    "SaveManager",
]
