"""Quicksave / quickload round-trip (WP-F3).

Boots the real app headlessly via GameDriver, redirects the save dir to a
temp path, then verifies F6=quicksave / F9=quickload behaviour:

- quicksave writes ``config.quicksave_slot`` to disk;
- quickload restores the saved flags + location and drops any mutations
  made after the save;
- the transient ``__``-prefixed meta bridges (plugin manager, autosave
  config) survive the state swap.
"""
import os
import types

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture
def driver(tmp_path):
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    # Redirect saves to a temp dir so the test never touches the repo.
    save_dir = tmp_path / "saves"
    save_dir.mkdir(parents=True, exist_ok=True)
    d.app.config.save_dir = types.MethodType(lambda self, _p=save_dir: _p,
                                             d.app.config)
    # Clear data shares the production save directory but its path/object are
    # resolved during app construction. Isolate them too so NG+ tests neither
    # read nor write the developer's real profile.
    from world_gal_game.core.clear_data import ClearData
    d.app._clear_data_path = save_dir / "clear_data.json"
    d.app.clear_data = ClearData()
    d.app.state.meta["__clear_data__"] = d.app.clear_data
    # Point the live autosave bridge at the same temp dir.
    bridge = d.app.state.meta.get("__autosave_config__")
    if bridge is not None:
        bridge["save_dir"] = save_dir
    d._save_dir = save_dir
    yield d
    d.quit()


def test_quicksave_writes_slot(driver):
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    driver.app._quicksave()
    slot = driver.app.config.quicksave_slot
    assert (driver._save_dir / f"{slot}.json").exists()


def test_quicksave_then_quickload_round_trips_state(driver):
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)

    loc_before = driver.app.state.map.current_location_id
    driver.app.state.events.set_flag("qs_marker", True)

    driver.app._quicksave()

    # Mutate after the save: flip the marker and add a brand-new flag.
    driver.app.state.events.set_flag("qs_marker", False)
    driver.app.state.events.set_flag("post_save_only", True)

    driver.app._quickload()
    driver.advance_frames(3)

    # Saved value restored; post-save mutation discarded.
    assert driver.app.state.events.flags.get("qs_marker") is True
    assert "post_save_only" not in driver.app.state.events.flags
    assert driver.app.state.map.current_location_id == loc_before


def test_quickload_unions_clear_data_into_older_save_trackers(driver):
    """Quickload keeps save progress and restores newer global completion."""
    from world_gal_game.core.media_authority import build_media_authority

    app = driver.app
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)

    # These two synthetic paths represent still-shipped pack/plugin CGs. Save
    # and clear-data progress may unlock current authority, but cannot invent
    # a decoder path that the freshly loaded pack did not register.
    app._authored_media_authority = build_media_authority(
        app._authored_story_catalog,
        additional_cg_ids={
            "assets/cgs/save-only.png",
            "assets/cgs/global.png",
        },
    )
    app.state.meta["__pack_media_authority__"] = (
        app._authored_media_authority
    )

    app.state.cg_gallery.unlock("assets/cgs/save-only.png")
    app.state.endings.unlocked["ending_friend"] = "2026-01-01T00:00:00"
    app.state.endings.seen.add("ending_friend")
    app._quicksave()

    # Model completion earned after that save was written.
    app.clear_data.scenes_seen.add("meet_heroine")
    app.clear_data.cgs_seen.add("assets/cgs/global.png")
    app.clear_data.endings_seen.update({
        "ending_lover",
        "ending_removed_from_pack",
    })

    app._quickload()

    assert "meet_heroine" in app.state.read_log.scenes
    assert app.state.cg_gallery.is_unlocked("assets/cgs/save-only.png")
    assert app.state.cg_gallery.is_unlocked("assets/cgs/global.png")
    assert app.state.endings.is_unlocked("ending_friend")
    assert app.state.endings.is_unlocked("ending_lover")
    assert "ending_removed_from_pack" not in app.state.endings.unlocked
    assert "ending_lover" in app.state.endings.seen
    assert "ending_lover" in {
        ending.id for ending in app.state.endings.visible_to_player()
    }


def test_quickload_rebinds_ending_added_after_save_was_created(driver):
    app = driver.app
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)

    ending_id = next(iter(app._authored_ending_catalog.endings))
    # Simulate a save from an older pack release that had no definition for
    # this current ending. The immutable authored baseline remains current.
    removed = app.state.endings.endings.pop(ending_id)
    app._quicksave()
    app.state.endings.register(removed)
    app.clear_data.endings_seen.add(ending_id)

    app._quickload()

    assert app.state.endings.get(ending_id) is not None
    assert app.state.endings.is_unlocked(ending_id)
    assert ending_id in {
        ending.id for ending in app.state.endings.visible_to_player()
    }
    assert ending_id not in {
        ending.id for ending in app.state.endings.newly_unlocked()
    }


def test_quickload_rebinds_authored_story_and_choice_resume(
    driver,
):
    import json

    from world_gal_game.core.story_graph import (
        Choice,
        ChoiceCardSpec,
        Condition,
        Effect,
        Line,
        Scene,
    )

    app = driver.app
    app.config.autosave_enabled = False
    app.state.player.name = "存檔玩家"
    app.state.events.set_flag("saved_choice_gate", True)

    scene_id = "quickload_choice_card_rebind_probe"
    choice_id = "keep_saved_route_logic"
    saved_choice = Choice(
        id=choice_id,
        text="存檔版本的功能文字",
        requires=[Condition(kind="flag", target="saved_choice_gate")],
        forbids=[Condition(kind="flag", target="saved_choice_block")],
        effects=[
            Effect(
                kind="set_flag",
                target="saved_choice_effect",
                value=True,
            ),
        ],
        next_scene="saved_choice_destination",
        hidden_if_locked=True,
        card=ChoiceCardSpec(title="存檔中的舊卡片"),
    )
    app.state.story.add_scene(Scene(
        id=scene_id,
        lines=[Line(text="抵達真正的選擇邊界。")],
        choices=[saved_choice],
    ))

    current_card = ChoiceCardSpec(
        title="{player_name}目前版本的卡片",
        subtitle="只允許這份視覺 metadata 被重新綁定",
        action="閱讀目前的卡片",
    )
    current_choice = Choice(
        id=choice_id,
        text="新版 authored 功能文字",
        requires=[
            Condition(kind="flag", target="current_choice_gate"),
        ],
        effects=[
            Effect(
                kind="set_flag",
                target="current_choice_effect",
                value=True,
            ),
        ],
        next_scene="current_choice_destination",
        card=current_card,
    )
    app._authored_story_catalog.add_scene(Scene(
        id=scene_id,
        lines=[Line(text="新版 authored line 取代存檔定義。")],
        choices=[current_choice],
    ))

    app._start_dialogue(scene_id)
    app.manager.commit_pending()
    driver.advance_frames(1)
    dialogue = app.manager.current
    assert type(dialogue).__name__ == "DialogueScene"
    dialogue.box.force_reveal()
    dialogue._advance()
    assert dialogue._current_presentation.kind == "choice"
    assert dialogue._current_presentation.choices[0].card.title == \
        "存檔中的舊卡片"

    app._quicksave()
    slot = app.config.quicksave_slot
    raw = json.loads(
        (driver._save_dir / f"{slot}.json").read_text(encoding="utf-8"),
    )
    assert raw["story"]["scenes"][scene_id]["choices"][0]["card"]["title"] == \
        "存檔中的舊卡片"
    saved_resume_row = raw["_resume"]["presentation"]["choices"][0]
    assert saved_resume_row["text"] == "存檔版本的功能文字"
    assert saved_resume_row["enabled"] is True
    assert saved_resume_row["card"]["title"] == "存檔中的舊卡片"

    app._quickload()
    driver.advance_frames(2)

    loaded_choice = app.state.story.get(scene_id).choices[0]
    assert loaded_choice == current_choice
    assert app.state.story.get(scene_id).lines[0].text == \
        "新版 authored line 取代存檔定義。"

    restored = app.manager.current
    assert type(restored).__name__ == "DialogueScene"
    restored_resume = restored.export_resume_context()
    assert restored_resume is not None
    restored_row = restored_resume["presentation"]["choices"][0]
    assert restored_row["text"] == saved_resume_row["text"]
    assert restored_row["enabled"] == saved_resume_row["enabled"]
    assert restored_row["reason"] == saved_resume_row["reason"]
    assert restored_row["card"]["title"] == "存檔玩家目前版本的卡片"
    assert restored_row["card"]["subtitle"] == \
        "只允許這份視覺 metadata 被重新綁定"


def test_quickload_saved_story_cannot_inject_next_line_or_history_voice(
    driver,
):
    import json

    from world_gal_game.core.story_graph import Effect, Line, Scene

    app = driver.app
    scene_id = "runtime_authorized_story_rebind_probe"
    safe_voice = "assets/voice/current-authority.ogg"
    authored_scene = Scene(
        id=scene_id,
        lines=[
            Line(text="已經顯示過的第一句"),
            Line(
                speaker="目前版本",
                text="由目前 pack 定義的下一句",
                portrait="assets/portraits/current-authority.png",
                cg="assets/cgs/current-authority.png",
                sfx="assets/audio/current-sfx.ogg",
                bgm="assets/audio/current-bgm.ogg",
                ambient="assets/audio/current-ambient.ogg",
                voice=safe_voice,
                effects=[
                    Effect(
                        kind="set_flag",
                        target="current_authored_effect",
                        value=True,
                    ),
                ],
            ),
        ],
    )
    # A plugin/runtime scene is authority only after this runtime registers it
    # in the catalogue used by the load boundary.
    app._authored_story_catalog.add_scene(authored_scene)
    app.state.story.add_scene(authored_scene.model_copy(deep=True))
    app.state.story.played = {scene_id}
    app.state.story.current_scene = scene_id
    app.state.story.current_line_index = 1
    app.state.dialogue_history.lines = [
        {"speaker": "目前版本", "text": "可信", "voice": safe_voice},
        {"speaker": "未知", "text": "待竄改", "voice": None},
    ]
    app._quicksave()

    slot = app.config.quicksave_slot
    path = driver._save_dir / f"{slot}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    forged_line = raw["story"]["scenes"][scene_id]["lines"][1]
    forged_line.update({
        "speaker": "save JSON",
        "text": "竄改後的下一句",
        "portrait": "/tmp/forged-portrait.png",
        "cg": "/tmp/forged-cg.png",
        "sfx": "/tmp/forged-sfx.ogg",
        "bgm": "/tmp/forged-bgm.ogg",
        "ambient": "/tmp/forged-ambient.ogg",
        "voice": "/tmp/forged-voice.ogg",
        "effects": [{
            "kind": "set_flag",
            "target": "forged_saved_effect",
            "value": True,
        }],
    })
    raw["story"]["scenes"]["save_json_only_scene"] = {
        "id": "save_json_only_scene",
        "lines": [{
            "text": "存檔不可新增可執行 scene",
            "effects": [{
                "kind": "set_flag",
                "target": "forged_extra_scene_effect",
            }],
        }],
    }
    raw["story"]["played"].append("save_json_only_scene")
    raw["dialogue_history"]["lines"][1]["voice"] = \
        "/tmp/forged-history-voice.ogg"
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert app._load_slot(slot) is True
    assert app.state.story.scenes == app._authored_story_catalog.scenes
    assert app.state.story.played == {scene_id}
    assert app.state.story.current_scene == scene_id
    assert app.state.story.current_line_index == 1
    assert "save_json_only_scene" not in app.state.story.scenes
    assert app.state.dialogue_history.lines[0]["voice"] == safe_voice
    assert app.state.dialogue_history.lines[1]["voice"] is None

    presentation = app.dialogue.next_line()
    assert presentation.line is not None
    assert presentation.line.text == "由目前 pack 定義的下一句"
    assert presentation.line.portrait == \
        "assets/portraits/current-authority.png"
    assert presentation.line.cg == "assets/cgs/current-authority.png"
    assert presentation.line.bgm == "assets/audio/current-bgm.ogg"
    assert presentation.line.ambient == "assets/audio/current-ambient.ogg"
    assert presentation.line.voice == safe_voice
    assert app.state.events.has_flag("current_authored_effect")
    assert not app.state.events.has_flag("forged_saved_effect")
    assert not app.state.events.has_flag("forged_extra_scene_effect")


@pytest.mark.parametrize(
    ("key", "forged"),
    [
        ("current_bgm", "assets/bgm/save-only-carry.ogg"),
        ("current_ambient", "assets/sfx/save-only-carry.ogg"),
    ],
)
def test_quickload_legacy_save_drops_unowned_audio_carry(
    driver,
    key,
    forged,
):
    import json

    app = driver.app
    app._quicksave()
    slot = app.config.quicksave_slot
    path = driver._save_dir / f"{slot}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("_resume", None)
    raw.setdefault("meta", {})[key] = forged
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert app._load_slot(slot) is True
    assert key not in app.state.meta


def test_quickload_rebinds_gallery_and_music_unlocks_to_pack_authority(
    driver,
):
    import json

    from world_gal_game.core.media_authority import build_media_authority

    app = driver.app
    allowed_cg = "assets/cgs/current-pack-memory.png"
    allowed_music = "assets/bgm/current-pack-theme.ogg"
    app._authored_media_authority = build_media_authority(
        app._authored_story_catalog,
        additional_cg_ids={allowed_cg},
        additional_music_ids={allowed_music},
    )
    app.state.meta["__pack_media_authority__"] = (
        app._authored_media_authority
    )
    app.state.cg_gallery.unlock(allowed_cg)
    app.state.music_room.unlock(allowed_music)
    app._quicksave()

    slot = app.config.quicksave_slot
    path = driver._save_dir / f"{slot}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["cg_gallery"]["unlocked"].extend([
        "/tmp/save-forged-cg.png",
        "assets/cgs/../../outside.png",
        "assets/cgs/retired.png",
    ])
    raw["music_room"]["unlocked"].extend([
        "/tmp/save-forged-theme.ogg",
        "../outside.ogg",
        "assets/bgm/retired.ogg",
    ])
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Cross-playthrough data is another user-writable input and must not
    # launder an unknown CG back into the freshly rebound slot.
    app.clear_data.cgs_seen.update({
        allowed_cg,
        "/tmp/clear-data-forged-cg.png",
    })

    assert app._load_slot(slot) is True
    assert app.state.cg_gallery.unlocked == {allowed_cg}
    assert app.state.music_room.unlocked == {allowed_music}


def test_quickload_strips_save_only_replay_visual_effects(driver):
    import json

    from world_gal_game.core.story_graph import Line, Scene
    from world_gal_game.dialogue.dialogue_engine import REPLAY_FX_PAYLOAD_KEY

    app = driver.app
    scene_id = "quickload_replay_fx_authority_probe"
    authored = Scene(
        id=scene_id,
        lines=[Line(text="目前版本沒有背景切換效果。")],
    )
    app._authored_story_catalog.add_scene(authored)
    app.state.story.add_scene(authored.model_copy(deep=True))
    app.dialogue.start_scene(scene_id)
    app.dialogue.next_line()
    assert app.state.read_log.replay_entry(scene_id) is not None
    app._quicksave()

    slot = app.config.quicksave_slot
    path = driver._save_dir / f"{slot}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["read_log"]["replay_archive"][scene_id]["presentations"][0][
        REPLAY_FX_PAYLOAD_KEY
    ] = [{
        "fx": "set_background",
        "path": "assets/backgrounds/save-only.png",
    }]
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert app._load_slot(slot) is True
    loaded = app.state.read_log.replay_entry(scene_id)
    assert loaded is not None
    assert REPLAY_FX_PAYLOAD_KEY not in loaded.presentations[0]


def test_quickload_rejects_saved_only_active_scene_atomically(driver):
    import json

    app = driver.app
    app.state.player.name = "存檔中的名字"
    app._quicksave()
    slot = app.config.quicksave_slot
    path = driver._save_dir / f"{slot}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["story"]["scenes"]["save_json_only_scene"] = {
        "id": "save_json_only_scene",
        "lines": [{"text": "不可執行"}],
    }
    raw["story"]["current_scene"] = "save_json_only_scene"
    raw["story"]["current_line_index"] = 0
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    app.state.player.name = "載入失敗後仍保留的名字"
    state_before = app.state.model_dump(mode="json")

    assert app._load_slot(slot) is False
    assert app.state.model_dump(mode="json") == state_before
    assert "save_json_only_scene" not in app.state.story.scenes


def test_quickload_preserves_transient_bridges(driver):
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    driver.app._quicksave()
    driver.app._quickload()
    driver.advance_frames(2)
    # The plugin manager + autosave bridge are filtered out at save time
    # and must be re-attached after the state swap.
    assert "__plugin_manager__" in driver.app.state.meta
    assert "__autosave_config__" in driver.app.state.meta


def test_quickload_missing_slot_is_noop(driver):
    """Quickloading with no quicksave on disk must not crash or wipe state."""
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    loc = driver.app.state.map.current_location_id
    # No _quicksave() has run, so the slot is absent.
    driver.app._quickload()
    driver.advance_frames(2)
    assert driver.app.state.map.current_location_id == loc


def test_new_game_rebuilds_state_and_rebinds_every_runtime_reference(driver):
    """A second New Game is a fresh run, with only NG+ read data + title name."""
    from world_gal_game.scenes.title import TitleScene
    from world_gal_game.ui.widgets.toast import Toast

    app = driver.app
    app.config.autosave_enabled = False
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)

    old_state = app.state
    initial_location = old_state.map.current_location_id
    initial_inventory = dict(old_state.inventory.counts)
    initial_resources = dict(old_state.resources.values)
    heroine_id = next(iter(old_state.affection.characters))

    # Pollute every major per-playthrough subsystem that used to leak.
    old_state.events.set_flag("previous_run_only", True)
    old_state.time.advance(8)
    old_state.inventory.add("previous_run_item", 7)
    old_state.resources.adjust("previous_run_resource", 99)
    old_state.affection.adjust(heroine_id, 42)
    old_state.route = "previous_route"
    old_state.story.played.add("previous_run_scene")
    old_state.map.visited.add("previous_run_location")
    old_state.meta["__plugin:test_previous_run__"] = {"dirty": True}
    old_state.cg_gallery.unlock("assets/cgs/lover_lakeside.png")
    old_state.endings.unlocked["ending_lover"] = "2026-01-01T00:00:00"
    app.toast_stack.push(Toast("上一輪通知"))

    # Returning to title records completed scenes as cross-run read data.
    old_state.read_log.mark_scene_done("prologue")
    app._quit_to_title()
    app.manager.commit_pending()
    # Historical clear data can outlive pack content. It must remain on disk
    # without reviving a removed ending in the new live tracker.
    app.clear_data.endings_seen.add("ending_removed_from_pack")
    assert isinstance(app.manager.current, TitleScene)
    # clear_data.json now exists, but it is not a playable save and therefore
    # must not manufacture a Continue action on the title screen.
    assert app.manager.current.on_continue is None

    # Exercise the real title-name path: TitleScene writes this onto the old
    # state immediately before invoking _start_new_game.
    title = app.manager.current
    assert title.name_input is not None
    title.name_input.text = "第二輪主角"
    title._start_new()
    app.manager.commit_pending()

    fresh = app.state
    assert fresh is not old_state
    assert fresh.player.name == "第二輪主角"
    assert "previous_run_only" not in fresh.events.flags
    assert (fresh.time.day, fresh.time.phase_index) == (1, 0)
    assert fresh.inventory.counts == initial_inventory
    assert fresh.resources.values == initial_resources
    assert fresh.affection.get(heroine_id) == 0
    assert fresh.route is None
    assert "previous_run_scene" not in fresh.story.played
    assert "previous_run_location" not in fresh.map.visited
    assert fresh.map.current_location_id == initial_location
    assert "__plugin:test_previous_run__" not in fresh.meta
    assert app.toast_stack.toasts == []

    # Deliberate NG+ semantics survive the reset.
    assert fresh.meta["__clear_data__"] is app.clear_data
    assert "prologue" in fresh.read_log.scenes
    assert fresh.cg_gallery.is_unlocked("assets/cgs/lover_lakeside.png")
    assert fresh.endings.is_unlocked("ending_lover")
    assert "ending_lover" in {
        ending.id for ending in fresh.endings.visible_to_player()
    }
    assert "ending_removed_from_pack" not in fresh.endings.unlocked
    assert fresh.endings.newly_unlocked() == []

    # All stateful runtime owners now point at the fresh object.
    assert app.ctx.state is fresh
    assert app.dialogue.state is fresh
    assert app.ctx.dialogue is app.dialogue
    assert app.ctx.npcs is app.npcs
    bridge = fresh.meta["__autosave_config__"]
    assert bridge["config"] is app.config
    assert bridge["save_dir"] == driver._save_dir
    plugin_manager = fresh.meta["__plugin_manager__"]
    assert plugin_manager._ctx.state is fresh
    assert plugin_manager._ctx.config is app.config


def test_save_menu_thumbnail_wired_and_listed(driver):
    """``_open_save_menu`` now passes a screen grabber, so a save through
    SaveScene produces a thumbnail PNG that ``list_saves()`` reports."""
    from world_gal_game.core.save_manager import SaveManager

    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    driver.app._open_save_menu()
    driver.advance_frames(2)
    scene = driver.app.manager.current
    assert type(scene).__name__ == "SaveScene"
    assert scene._get_screen is not None
    # The first row in save mode is the "new save" item.
    scene._on_action(scene._items[0])
    driver.advance_frames(2)
    rows = SaveManager(driver._save_dir).list_saves()
    assert rows, "expected at least one save written"
    assert any(r["thumbnail_path"] is not None for r in rows)


def test_f6_f9_keys_bound_in_main_loop(driver, monkeypatch):
    """The F6 / F9 key bindings should invoke quicksave / quickload."""
    import pygame

    calls = {"save": 0, "load": 0}
    monkeypatch.setattr(driver.app, "_quicksave",
                        lambda: calls.__setitem__("save", calls["save"] + 1))
    monkeypatch.setattr(driver.app, "_quickload",
                        lambda: calls.__setitem__("load", calls["load"] + 1))

    # Feed a synthetic F6 then F9 keydown through a real _step call by
    # pushing them onto the pygame event queue the loop reads.
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F6))
    driver.app._step(driver.frame_dt)
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9))
    driver.app._step(driver.frame_dt)

    assert calls["save"] == 1
    assert calls["load"] == 1
