# Scenes

場景是一段「對白 + 選項」的單元，住在 `content/scenes/*.yaml`。
單一檔案內可以放單一場景或多個，引擎在啟動時把整個資料夾全部讀進來。

## 最小場景

```yaml
scenes:
  - id: meet_heroine
    title: "初遇 · 林清雪"
    location: town_square        # 觸發時把玩家當前地點當這個（可選）
    background: assets/backgrounds/town_square.png
    lines:
      - text: "你走到廣場。佈告欄前有個女生正踮著腳，貼著一張手寫的紙。"
      - speaker: "林清雪"
        text: "「不好意思，可以幫我看看這張上面寫得清楚嗎？」"
```

跑到場景的最後一行，沒有 `choices` 也沒有 `on_end.play_scene`，
場景就會結束，玩家回到探索畫面。

## Line（一行對白）

```yaml
- text: |
    很長的敘述可以跨多行。
    第二行。
  speaker: "玩家"                                       # 沒寫 = 旁白
  portrait: assets/characters/heroine_1_smile.png       # 立繪覆寫
  expression: smile                                     # 從 NPC 的 portrait_set 抓
  cg: assets/cgs/key_event.png                          # 滿版 CG
  bgm: assets/bgm/park_evening.ogg                      # 切換 BGM
  sfx: assets/bgm/door_open.wav                         # 一次性音效

  # 條件可見：條件不成立時整行被跳過。
  requires:
    - {kind: affection_gte, target: heroine_1, value: 30}

  # 演出時附帶 effect。
  effects:
    - {kind: affection, target: heroine_1, value: 3}

  # 把這行交給 LLM 即時生成（需要 ANTHROPIC_API_KEY）。
  llm_speaker: true
  llm_directive: "玩家剛打翻你的咖啡，請用驚訝但體貼的口氣回應。"
  # 上面失敗時的 fallback 文字。
  text: "（保險用的文字）"
```

## Choice（選項）

選項出現在所有 line 演完之後：

```yaml
choices:
  - id: friendly
    text: "「下次我請妳喝咖啡？」"
    requires:                           # 全部成立才會啟用
      - {kind: affection_gte, target: someone, value: 10}
    forbids:                            # 任何一個成立則禁用
      - {kind: flag, target: angry_at_player}
    effects:
      - {kind: affection, target: someone, value: 5}
      - {kind: set_flag, target: had_coffee_promise}
    next_scene: someone_route_1         # 接到下一個場景
    hidden_if_locked: false             # 條件不符時顯示為「(條件未達)」灰色按鈕

  - id: shorthand
    text: "用字串簡寫的 effects 也行"
    # 等同於 [{kind: affection, target: someone, value: -2}]
    effects: ["affection:someone=-2"]
```

`next_scene` 不寫的話，選完當下就把場景結束，回探索。

## Scene-level fields

```yaml
- id: lover_event
  title: "戀人 · 翻開素描本"
  location: park
  background: assets/backgrounds/park.png
  bgm: assets/bgm/lakeside.ogg
  route: heroine_1              # 標記這場景屬於哪條路線（純元資料）

  # 進場條件 — 不滿足時 scene_hook 不會自動觸發。
  # （但用 effect: play_scene 強制觸發時不會檢查。）
  requires:
    - {kind: flag, target: heroine_1_lover}
    - {kind: affection_gte, target: heroine_1, value: 80}

  # 此場景只能播一次（預設 true）。
  once: true

  # 用來分類，引擎不解釋這些字串。
  tags: [lover, climax]

  lines: [...]
  choices: [...]

  # 場景結束時自動執行的 effects（包括接到下一個場景）。
  on_end:
    - {kind: set_flag, target: arc_done}
    - {kind: affection, target: heroine_1, value: 20}
    - {kind: play_scene, target: ending_lover}
```

## 在地點觸發場景

`content/locations.yaml` 的 `scene_hooks` 把地點和場景連起來：

```yaml
- id: park
  name: "湖畔公園"
  background: assets/backgrounds/park.png
  exits: [town_square]
  scene_hooks:
    - scene_id: find_sketchbook_park
      trigger: examine            # enter / examine / auto / night_only
      requires_flags: [quest_started]
      forbids_flags: [obj_park_done]
      requires_time: [afternoon, evening]
      # Full condition gates are also supported when flags/time are not enough:
      requires:
        - {kind: affection_gte, target: someone, value: 25}
      forbids:
        - {kind: scene_played, target: route_lockout}
      once: true
```

- `trigger: enter` 或 `auto` → 玩家踏進地點時自動播
- `trigger: examine` → 探索畫面出現一顆「場景名」按鈕讓玩家自己點
- `requires_flags` / `forbids_flags` 是簡寫；`requires` / `forbids` 可使用完整 condition（好感度、任務、物品、plugin 條件等）。

## 全部 effect / condition kind

完整參考：
- [effects-reference.md](effects-reference.md)
- [conditions-reference.md](conditions-reference.md)

常用的：
- **effect**：`affection`、`set_flag`、`give_item`、`gain_resource`、`play_scene`
- **condition**：`flag`、`affection_gte`、`has_item`、`resource_gte`、`time_in`

## 字串簡寫

`effects:` 和 `requires:` 兩種寫法都可以：

```yaml
# 完整 dict
effects:
  - {kind: affection, target: heroine_1, value: 5}

# 簡寫 "kind:target=value"（適合單值的常用 effect）
effects: ["affection:heroine_1=5"]
```
