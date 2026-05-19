#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Scaffold a brand-new game pack ready to run on the engine.

Usage:
    uv run tools/scaffold_pack.py --pack my_game --title "我的遊戲"

This creates:

    games/my_game/
    ├── content/
    │   ├── meta.yaml           # title, start scene/location, theme overrides
    │   ├── locations.yaml      # 3 example places
    │   ├── characters.yaml     # 1 heroine + 1 side NPC
    │   └── scenes/
    │       ├── 00_prologue.yaml
    │       └── 10_meet_heroine.yaml
    └── assets/
        ├── backgrounds/  (empty — engine uses placeholders until you fill in)
        ├── characters/
        ├── cgs/
        └── bgm/

The created pack is immediately runnable:

    uv run python main.py --pack my_game
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parent.parent


META_TMPL = dedent('''\
    # Pack metadata for {pack}.

    # Schema version of the pack format. Engine reads it to migrate old
    # packs against future schema changes. Keep as "0.1" for new packs.
    pack_format_version: "0.1"

    title: "{title}"
    subtitle: "{subtitle}"

    text_speed: 45

    # Optional bundled font — relative to this pack's root.
    # bundled_font: assets/fonts/MyFont.ttf

    title_bg: assets/backgrounds/title.png

    start_location: starting_room
    intro_scene: prologue

    player:
      name: "玩家"
      pronouns: "他"

    # Optionally override the engine's defaults. Comment out keys you don't
    # care about — the engine will keep its bundled defaults for those.
    # theme:
    #   accent: [216, 80, 143]
    #   accent_alt: [107, 107, 255]
    #   accent_warm: [240, 198, 116]
    # locale:
    #   affection_levels:
    #     - {{min: 0,   label: "Stranger"}}
    #     - {{min: 25,  label: "Friend"}}
    #     - {{min: 100, label: "Lover"}}
    #   ui:
    #     new_game: "New Game"
''')


LOCATIONS_TMPL = dedent('''\
    locations:

      - id: starting_room
        name: "起點的房間"
        region: "序章"
        description: |
          故事開始的地方。把這段文字改成你的遊戲第一個場景的描述。
        background: assets/backgrounds/starting_room.png
        map_x: 10
        map_y: 50
        exits: [town]

      - id: town
        name: "街上"
        region: "城市"
        description: |
          一個熱鬧的小鎮街道。試著想像主角第一次走出家門的感覺。
        background: assets/backgrounds/town.png
        map_x: 30
        map_y: 50
        exits: [starting_room, park]
        npcs:
          - npc_id: heroine_1
            times: [afternoon, evening]
        scene_hooks:
          - scene_id: meet_heroine
            trigger: examine
            forbids_flags: [met_heroine]
            once: true

      - id: park
        name: "公園"
        region: "城市"
        description: |
          安靜的小公園。湖邊偶爾會有意外的相遇。
        background: assets/backgrounds/park.png
        map_x: 45
        map_y: 50
        exits: [town]
''')


CHARACTERS_TMPL = dedent('''\
    characters:

      - id: heroine_1
        name: "女主角A"
        role: "你的第一位女主角"
        age: 19
        is_heroine: true
        route_id: heroine_1
        portrait: assets/characters/heroine_1_normal.png
        portrait_set:
          smile: assets/characters/heroine_1_smile.png
        description: |
          請改寫這段描述，告訴 LLM 她的外表、個性、能感受到的細節。
        persona: |
          描述她說話的風格、她在不同情緒下會怎麼反應、她如何看待玩家。
        voice: "說話節奏與口氣描述（一句話）。"
        backstory: |
          她的過去 — 對 LLM 來說，這是『她記得而玩家還不知道』的事。
        likes: ["範例1", "範例2"]
        dislikes: ["範例3"]
        llm_brain: true
        thresholds:
          - name: "朋友"
            value: 25
            unlocks: ["heroine_1_friend"]
          - name: "戀人"
            value: 80
            unlocks: ["heroine_1_ending"]

      - id: townsperson
        name: "路人甲"
        role: "鎮上居民"
        portrait: assets/characters/townsperson.png
        description: "一個尋常的小鎮居民。"
        persona: "親切、好奇、講話愛離題。"
        llm_brain: true
''')


PROLOGUE_TMPL = dedent('''\
    scenes:

      - id: prologue
        title: "序章"
        location: starting_room
        background: assets/backgrounds/starting_room.png
        lines:
          - text: |
              這是你遊戲的第一段文字。
              改寫它，介紹你的世界。
          - text: |
              你呼吸了一口氣，準備推開門。
          - speaker: "玩家"
            text: "「該出發了。」"
        on_end:
          - kind: set_flag
            target: prologue_done
            value: true
''')


MEET_HEROINE_TMPL = dedent('''\
    scenes:

      - id: meet_heroine
        title: "初遇 · 女主角A"
        location: town
        background: assets/backgrounds/town.png
        lines:
          - text: |
              街上突然有人叫住你。
          - speaker: "女主角A"
            text: "「等等！你是新來的對吧？」"
            portrait: assets/characters/heroine_1_normal.png
            expression: smile
          - speaker: "女主角A"
            text: |
              「我叫A。
              下次來找我，我可以帶你逛這個小鎮。」
            expression: smile
        choices:
          - id: friendly
            text: "「好啊，謝謝妳！」"
            effects:
              - kind: affection
                target: heroine_1
                value: 5
              - kind: set_flag
                target: met_heroine
                value: true
          - id: shy
            text: "「呃，下次再說吧。」"
            effects:
              - kind: affection
                target: heroine_1
                value: 1
              - kind: set_flag
                target: met_heroine
                value: true
''')


FILES = [
    ("content/meta.yaml", META_TMPL),
    ("content/locations.yaml", LOCATIONS_TMPL),
    ("content/characters.yaml", CHARACTERS_TMPL),
    ("content/scenes/00_prologue.yaml", PROLOGUE_TMPL),
    ("content/scenes/10_meet_heroine.yaml", MEET_HEROINE_TMPL),
]
EMPTY_DIRS = [
    "assets/backgrounds",
    "assets/characters",
    "assets/cgs",
    "assets/bgm",
    "assets/ui",
    "assets/fonts",
]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pack", required=True,
                   help="pack directory name (e.g. my_game)")
    p.add_argument("--title", required=False, default=None,
                   help='display title (defaults to the pack name)')
    p.add_argument("--subtitle", required=False, default="",
                   help="optional subtitle shown on the title screen")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing pack directory")
    args = p.parse_args(argv)

    pack_dir = ROOT / "games" / args.pack
    if pack_dir.exists() and not args.force:
        print(f"error: {pack_dir} already exists (use --force to overwrite)",
              file=sys.stderr)
        return 1
    pack_dir.mkdir(parents=True, exist_ok=True)

    title = args.title or args.pack
    subtitle = args.subtitle

    for rel, body in FILES:
        target = pack_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        text = body.format(pack=args.pack, title=title, subtitle=subtitle)
        target.write_text(text, encoding="utf-8")
        print(f"[scaffold] wrote {target}")
    for rel in EMPTY_DIRS:
        d = pack_dir / rel
        d.mkdir(parents=True, exist_ok=True)
        keep = d / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")

    print()
    print(f"[scaffold] pack ready at {pack_dir}")
    print(f"[scaffold] try it:")
    print(f"    uv run python main.py --pack {args.pack}")
    print(f"    uv run python main.py --pack {args.pack} --headless --inspect")
    return 0


if __name__ == "__main__":
    sys.exit(main())
