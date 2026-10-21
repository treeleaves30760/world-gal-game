"""Current-pack authority for persistent gallery and music unlocks."""
from __future__ import annotations

from world_gal_game.core.cg_gallery import CGGallery
from world_gal_game.core.cg_gallery_spec import CGGalleryManifest
from world_gal_game.core.clear_data import ClearData
from world_gal_game.core.game_state import GameState
from world_gal_game.core.media_authority import (
    build_media_authority,
    canonical_pack_media_id,
    rebind_authorized_media_progress,
)
from world_gal_game.core.music_room import MusicRoom
from world_gal_game.core.story_graph import Choice, Effect, Line, Scene, StoryGraph


def _authored_story() -> StoryGraph:
    return StoryGraph(scenes={
        "media": Scene(
            id="media",
            cg="assets/cgs/scene.png",
            bgm="assets/bgm/scene.ogg",
            lines=[
                Line(
                    text="line",
                    cg="assets/cgs/line.png",
                    bgm="assets/bgm/line.ogg",
                    effects=[Effect(
                        kind="show_cg",
                        target=" assets/cgs/./line-effect.png ",
                    )],
                ),
                Line(
                    text="hostile authored input is not an authority escape",
                    cg="../outside.png",
                    bgm="/tmp/outside.ogg",
                ),
            ],
            choices=[Choice(
                id="effect_choice",
                text="choice",
                effects=[Effect(
                    kind="show_cg",
                    target="assets/cgs/choice-effect.png",
                )],
            )],
            on_end=[Effect(
                kind="show_cg",
                target="assets/cgs/end-effect.png",
            )],
        ),
        "silence": Scene(
            id="silence",
            bgm="none",
            lines=[Line(text="quiet", bgm="-")],
        ),
    })


def test_authority_collects_current_authored_media_without_thumbnail_identity():
    manifest = CGGalleryManifest.from_items([{
        "asset": "assets/cgs/manifest.png",
        "title": "Manifest memory",
        "thumbnail": "assets/cgs/thumbs/manifest.webp",
    }])

    authority = build_media_authority(
        _authored_story(),
        cg_manifest=manifest,
        additional_cg_ids={
            "assets/cgs/plugin.png",
            "../../plugin-escape.png",
        },
        additional_music_ids={
            "assets/bgm/directory.ogg",
            "C:\\outside\\track.ogg",
        },
    )

    assert authority.cg_ids == frozenset({
        "assets/cgs/scene.png",
        "assets/cgs/line.png",
        "assets/cgs/line-effect.png",
        "assets/cgs/choice-effect.png",
        "assets/cgs/end-effect.png",
        "assets/cgs/manifest.png",
        "assets/cgs/plugin.png",
    })
    assert "assets/cgs/thumbs/manifest.webp" not in authority.cg_ids
    assert authority.music_ids == frozenset({
        "assets/bgm/scene.ogg",
        "assets/bgm/line.ogg",
        "assets/bgm/directory.ogg",
    })


def test_media_id_canonicalizer_rejects_absolute_traversal_and_control_paths():
    assert canonical_pack_media_id(" assets/cgs/./memory.png ") == (
        "assets/cgs/memory.png"
    )
    assert canonical_pack_media_id("/etc/passwd") is None
    assert canonical_pack_media_id("C:\\Windows\\system.ini") is None
    assert canonical_pack_media_id("assets/cgs/../../etc/passwd") is None
    assert canonical_pack_media_id("../outside.png") is None
    assert canonical_pack_media_id("https://example.invalid/image.png") is None
    assert canonical_pack_media_id("assets/cgs/bad\x00.png") is None
    assert canonical_pack_media_id("assets/" + "a" * 1018) is None


def test_candidate_unlocks_are_deep_copied_and_intersected_with_authority():
    authority = build_media_authority(
        _authored_story(),
        additional_music_ids={"assets/bgm/directory.ogg"},
    )
    saved_cgs = CGGallery(unlocked={
        "assets/cgs/scene.png",
        "assets/cgs/./line.png",
        "assets/cgs/retired.png",
        "/etc/passwd",
        "../outside.png",
    })
    saved_music = MusicRoom(unlocked={
        "assets/bgm/line.ogg",
        "assets/bgm/./directory.ogg",
        "assets/bgm/retired.ogg",
        "/tmp/hostile.ogg",
        "..\\outside.ogg",
    })
    original_cgs = set(saved_cgs.unlocked)
    original_music = set(saved_music.unlocked)

    rebound_cgs, rebound_music = rebind_authorized_media_progress(
        saved_cgs,
        saved_music,
        authority,
    )

    assert rebound_cgs is not saved_cgs
    assert rebound_music is not saved_music
    assert rebound_cgs.unlocked == {
        "assets/cgs/scene.png",
        "assets/cgs/line.png",
    }
    assert rebound_music.unlocked == {
        "assets/bgm/line.ogg",
        "assets/bgm/directory.ogg",
    }
    assert saved_cgs.unlocked == original_cgs
    assert saved_music.unlocked == original_music


def test_fresh_initial_trackers_can_extend_current_pack_authority():
    initial_cgs = CGGallery(unlocked={"assets/cgs/plugin-initial.png"})
    initial_music = MusicRoom(unlocked={"assets/bgm/plugin-initial.ogg"})
    authority = build_media_authority(
        StoryGraph(),
        additional_cg_ids=initial_cgs.unlocked,
        additional_music_ids=initial_music.unlocked,
    )

    rebound_cgs, rebound_music = rebind_authorized_media_progress(
        initial_cgs,
        initial_music,
        authority,
    )

    assert rebound_cgs.unlocked == {"assets/cgs/plugin-initial.png"}
    assert rebound_music.unlocked == {"assets/bgm/plugin-initial.ogg"}


def test_clear_data_record_prunes_existing_and_incoming_unauthorized_cgs():
    state = GameState()
    state.cg_gallery.unlocked = {
        "assets/cgs/allowed.png",
        "assets/cgs/save-forged.png",
        "/etc/passwd",
    }
    clear_data = ClearData(cgs_seen={
        "assets/cgs/old-forged.png",
        "../outside.png",
    })

    changed = clear_data.record_from_state(
        state,
        authored_cg_ids={"assets/cgs/allowed.png"},
    )

    assert changed is True
    assert clear_data.cgs_seen == {"assets/cgs/allowed.png"}
    assert clear_data.record_from_state(
        state,
        authored_cg_ids={"assets/cgs/allowed.png"},
    ) is False


def test_clear_data_apply_never_injects_cgs_outside_caller_authority():
    state = GameState()
    state.cg_gallery.unlock("assets/cgs/existing-save.png")
    clear_data = ClearData(cgs_seen={
        "assets/cgs/allowed.png",
        "assets/cgs/forged.png",
        "/etc/passwd",
        "../outside.png",
    })

    changed = clear_data.apply_to_state(
        state,
        authored_cg_ids={"assets/cgs/allowed.png"},
    )

    assert changed is True
    assert state.cg_gallery.unlocked == {
        "assets/cgs/existing-save.png",
        "assets/cgs/allowed.png",
    }
    assert clear_data.apply_to_state(
        state,
        authored_cg_ids={"assets/cgs/allowed.png"},
    ) is False


def test_clear_data_record_prunes_unauthorized_music_and_is_idempotent():
    state = GameState()
    state.music_room.unlocked = {
        "assets/bgm/allowed.ogg",
        "assets/bgm/save-forged.ogg",
        "/tmp/outside.ogg",
    }
    clear_data = ClearData(music_seen={
        "assets/bgm/retired.ogg",
        "../outside.ogg",
    })

    changed = clear_data.record_from_state(
        state,
        authored_music_ids={"assets/bgm/allowed.ogg"},
    )

    assert changed is True
    assert clear_data.music_seen == {"assets/bgm/allowed.ogg"}
    assert clear_data.record_from_state(
        state,
        authored_music_ids={"assets/bgm/allowed.ogg"},
    ) is False


def test_clear_data_apply_unions_only_authorized_music():
    state = GameState()
    state.music_room.unlock("assets/bgm/save-only.ogg")
    clear_data = ClearData(music_seen={
        "assets/bgm/global.ogg",
        "assets/bgm/retired.ogg",
        "/tmp/outside.ogg",
    })

    changed = clear_data.apply_to_state(
        state,
        authored_music_ids={"assets/bgm/global.ogg"},
    )

    assert changed is True
    assert state.music_room.unlocked == {
        "assets/bgm/save-only.ogg",
        "assets/bgm/global.ogg",
    }
    assert clear_data.apply_to_state(
        state,
        authored_music_ids={"assets/bgm/global.ogg"},
    ) is False
