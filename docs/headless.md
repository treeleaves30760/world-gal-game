# Headless Mode

引擎可以完全無視窗執行 — 沒有 pygame window、沒有音效裝置、沒有顯示器
也跑得起來。這個模式是給：

- **CI** — 跑通關 smoke test，守住「主路線可以走到結局」
- **AI agent** — 我（Claude）可以驅動引擎、看狀態、自動驗證
- **整合測試** — 你自己寫的 pytest 套件

> Agent 操控/驗證的完整契約（`run_script` 的 `apply`/`check`/`assert`/
> `affordances`/`snapshot`/`restore` ops、per-op `diff`、execution `transcript`、
> JSON-Schema 匯出、determinism seed）見
> [ai-native-contract.md](ai-native-contract.md)。

## 印當前狀態

```bash
uv run world-gal-game --pack demo_pack --headless --inspect
```

輸出一大坨 JSON，包含：

```json
{
  "pack": "demo_pack",
  "title": "...",
  "subtitle": "...",
  "locale": {
    "requested": "en",
    "effective": "en",
    "source": "zh-Hant-TW",
    "default": "zh-Hant-TW"
  },
  "player": {"name": "玩家", "pronouns": "他"},
  "time": {
    "day": 1,
    "weekday": "mon",
    "weekday_label": "Mon",
    "time_of_day": "morning",
    "time_label": "Morning",
    "label": "Day 1 · Mon · Morning",
    ...
  },
  "location": "starting_room",
  "exits": ["town_square"],
  "all_locations": [...],
  "npcs_present": [...],
  "all_characters": [{"id": "heroine_1", "name": "林清雪", "affection": 0, ...}],
  "scenes_available": [...],
  "scenes_played": [...],
  "current_scene": null,
  "current_line_index": 0,
  "inventory": {"jasmine_tea": 1},
  "achievements": {"unlocked": [], "total": 9},
  "flags": {},
  "recent_events": [...]
}
```

`title`、`subtitle`、時間／星期／好感標籤、對白、說話者、聲音字幕、
choice 與 choice-card copy 都是目前 `effective` locale 的 presentation。
ID、flags、conditions、effects、transitions 與 asset path 仍是 source pack 權威；
不要把文字變化當成遊戲狀態變化。

## 跑一串動作

寫一個 JSON 腳本（demo_pack 已經內建 `games/demo_pack/scripts/test_lover_route.json`）：

```json
{
  "commands": [
    {"op": "start_scene", "scene": "prologue"},
    {"op": "next", "count": 8},
    {"op": "move", "location": "town_square"},
    {"op": "start_scene", "scene": "meet_heroine"},
    {"op": "next", "count": 8},
    {"op": "choose", "choice": "accept_quest"},
    {"op": "adjust_affection", "npc": "heroine_1", "delta": 75},
    {"op": "move", "location": "park"},
    {"op": "start_scene", "scene": "find_sketchbook_park"},
    {"op": "next", "count": 10},
    {"op": "choose", "choice": "chat_more"},
    {"op": "next", "count": 15},
    {"op": "choose", "choice": "confess"},
    {"op": "inspect"}
  ]
}
```

這個範例刻意使用 `start_scene`、`adjust_affection` 做聚焦的路線測試；它們是
開發者控制，不代表玩家在 UI 中可以跳過地點、時間或前置條件。若要驗證玩家可實際
走出的路徑，應以 `move`、`examine`、`gift`、`next`、`choose`、
`advance_time` 驅動，並先讀 `affordances`。

跑：

```bash
uv run world-gal-game --pack demo_pack --headless \
    --script games/demo_pack/scripts/test_lover_route.json
```

`games/demo_pack/scripts/` 底下還有 `test_friend_route.json`、`test_alone_route.json`
分別示範朋友 / 孤獨結局。

預設輸出包含每個 op 的結果 + 最後一個 `inspect` 的完整 snapshot。
用 `--no-inspect-after` 關掉最後那個 dump。

## 所有 op

| op | 必要參數 | 說明 |
|---|---|---|
| `inspect` | — | 把當前 snapshot 塞進 result |
| `locale` | `locale` | 在沒有 active dialogue 時切換已預載且驗證的語系；不寫入玩家設定 |
| `move` | `location`; `distant`（預設 false） | 玩家移動；對話開啟時拒絕。預設只走一條 authored exit；`distant: true` 以同一份不可變行程報價原子走完整條路。時間只增加路線明示的 `travel_cost` 總和 |
| `examine` | `scene` | 玩家調查當前地點可用的 `trigger: examine` hook；會重驗地點、時間、flags、`once` 與場景存在性 |
| `gift` | `npc`, `item`; `count`（只接受 1） | 玩家送出一件持有且未鎖定的物品；NPC 必須在當前地點、星期與時段實際出現 |
| `start_scene` | `scene` | 開發者直接開場景；不經玩家的地點、時間與 hook 可用性驗證 |
| `next` | `count` (預設 1) | 推進 N 行對白；遇到 choice/end 早退 |
| `choose` | `choice` | 選一個選項 id |
| `chat` | `npc`, `message` | 對 NPC 自由對話一次 |
| `advance_time` | `phases` (預設 1) | 玩家逐時段推進，遇到第一個可用的 `enter`/`auto` hook 就停下並開場景；回傳實際的 `phases_advanced` |
| `set_flag` | `key`, `value` (預設 true) | 開發者直接設旗標；會觸發成就重評 |
| `adjust_affection` | `npc`, `delta`, `stat` (預設 "affection") | 開發者直接調整好感 |
| `apply` | `effect` | 開發者直接套用任意已註冊 effect |
| `check` | `condition` | 評估任意已註冊 condition |
| `assert` | 依 assertion form | 驗證 flag、好感、已讀場景、結局或 condition |
| `affordances` | — | 回傳目前可見選項、出口、遠距行程及 structured `examine`/`gift` actions |
| `snapshot` | `name`（預設 "default"） | 儲存具名狀態快照 |
| `restore` | `name`（預設 "default"） | 還原具名狀態快照 |

`move`、`examine` 與 `gift` 都要求目前沒有對話；對話開啟時不只會拒絕操作，
`affordances()["examine"]` 與 `affordances()["gift"]` 也會是空陣列。
同一時段的 exits 會標成 unavailable，`travel_options`／`guided_destinations` 會清空，
`affordances()["map"]` 也會附上先讀完對話的鎖定理由。
`advance_time` 同樣不會在對話中執行，且 `phases` 必須至少為 1。即使要求一次
推進多個時段，引擎也會每一時段重新檢查 action policy 與 ambient hook；因此
`phases_advanced` 可能小於要求值。

## Headless 語系邊界

`HeadlessSession.open()` 先讀 `EngineConfig.locale`（embedding/runtime override），
否則讀取該 pack id 在 `locale_preferences` 的設定，最後由 manifest
`default` / `source` 保守回退。每個 session 擁有自己的 localization authority；
同一個 Python process 可以同時跑英文與中文 session，時間與好感標籤不會串線。

```python
english = HeadlessSession.open(
    EngineConfig(locale="en"),
    pack="my_pack",
)
assert english.inspect()["locale"]["effective"] == "en"

# 相當於 run_script 的 {"op": "locale", "locale": "zh-Hant-TW"}
result = english.set_locale("zh-Hant-TW")
```

`set_locale()` / `locale` 只在 `current_scene` 為 `null` 時成功。對話已開啟時會回傳
`ok: false`、保留原本 `effective` locale，也不重跑任何 line effect。
請先讓 scene 正常結束，再切換與開啟新 scene。不支援、不安全或未通過
catalog 驗證的 locale 會回退 manifest default/source，不會用輸入字串尋找檔案。

Headless 切換是當前 session 的 presentation control，**不會**呼叫
`EngineConfig.set_preferred_locale()` 或寫入 `settings.json`。需要持久化的 embedding
應在驗證 `effective` 後明確儲存 per-pack preference。

`affordances()` 的玩家調查與送禮項目是可直接送回 `run_script` 的 structured
actions，例如：

```json
{
  "examine": [
    {"op": "examine", "scene": "inspect_archive", "title": "查看校史檔案"}
  ],
  "gift": [
    {
      "op": "gift",
      "npc": "heroine_1",
      "npc_name": "林清雪",
      "item": "jasmine_tea",
      "item_name": "茉莉花茶",
      "count": 1,
      "owned": 2
    }
  ]
}
```

`start_scene`、`apply`、`set_flag` 與 `adjust_affection` 適合建立聚焦測試狀態；
不要用它們作為「玩家真的能走到這裡」的證據。資源、取得物品、使用物品、買賣等
尚未有獨立玩家 op 時，仍可由 `apply` 驅動對應 effect 做開發驗證。

## 自動跟著場景轉場

`next_line()` 和 `choose()` 如果遇到 `on_end: play_scene` 觸發的場景連接，
會**自動繼續播下一個場景的第一行**。對話真正結束後也會重新檢查當前地點第一個
可用的 `enter`/`auto` hook，與圖形介面關閉對話後的行為一致；若該完成場景被
`travel.spatial_map.auto_open_after_scenes` 指定為先交還地圖，則不搶先開 hook。
所以一條主線通常一個 script 就跑得完。

## 聲音字幕與 headless presentation

Line author 的 `sound_captions` 會以目前 locale 的已驗證文案出現在
`start_scene()`／`next_line()` 回傳的
`LinePresentation.sound_captions`，每筆是 `{kind, text}`。它是目前 logical line 的
靜態敘事語意，不受圖形介面的「顯示聲音字幕」玩家偏好影響，也不會因為 headless
環境沒有 mixer 或音檔而消失。Agent 因此能驗證靜音玩家是否仍取得關鍵 cue，但不能
把 caption 的存在當成音檔已交付、能解碼或混音已通過的證據。

## Choice card 與 action-space 邊界

若 choice author 了 typed `card` presentation，`start_scene()`／`next_line()` 在
真正抵達 choice boundary 後，會把它放在對應 choice row 的 `card` key；`text`
仍是永遠存在的功能標籤。這些文案同樣經過目前 locale 的
presentation-only lookup：

```json
{
  "id": "follow_archivist",
  "text": "翻到檔案員那一頁……",
  "enabled": true,
  "card": {
    "title": "檔案員",
    "subtitle": "校史館志工",
    "image": "assets/characters/archivist/smile.png",
    "image_alt": "檔案員抱著紅色精裝書的半身立繪",
    "image_fit": "cover",
    "image_focus_y": 0.34,
    "image_zoom": 1.1,
    "accent": [74, 150, 176],
    "details": [],
    "tags": [],
    "action": "翻到她那一頁"
  }
}
```

`affordances()["choices"]` 只在相同 choice presentation 已經顯示時才列出這些
rows，並沿用當次插值後的 text/card、enabled、reason 與 structured
`blocked_by`。它不會在還在讀 scene lines 時預告選項，也不會洩漏
`hidden_if_locked` card。這讓 headless、GUI 與 agent action space 使用同一個
可見邊界。`examine`／`gift` action 也遵守同一個對話邊界：對話開啟時不會出現在
affordances，且直接呼叫會被拒絕。

## 寫成 pytest

```python
# tests/test_lover_route.py
from world_gal_game.config import EngineConfig
from world_gal_game.headless import HeadlessSession


def test_lover_full_route():
    sess = HeadlessSession.open(EngineConfig(), pack="demo_pack")
    sess.start_scene("prologue")
    sess.next_line(8)
    sess.move_to("town_square")
    sess.start_scene("meet_heroine")
    sess.next_line(8)
    sess.choose("accept_quest")
    sess.adjust_affection("heroine_1", 75)
    sess.move_to("park")
    sess.start_scene("find_sketchbook_park")
    sess.next_line(20)
    sess.choose("chat_more")
    sess.next_line(15)
    sess.choose("confess")
    sess.next_line(10)
    snap = sess.inspect()

    flags = snap["flags"]
    assert flags.get("ending_lover")
    heroine = next(c for c in snap["all_characters"] if c["id"] == "heroine_1")
    assert heroine["affection"] >= 80
```

若 pack 啟用 `travel.scope: graph`，`sess.affordances()` 另提供
`travel_options`，包含路徑、總成本、抵達星期／時段、抵達 NPC 與保證觸發的場景。
程式可用 `sess.move_to("far_location", allow_distant=True)` 提交遠距行程；
直接移動的舊語意不變。`allow_distant=True` 只代表呼叫端願意提交遠距行程，
不會繞過 `travel.requires_flags`；gate 尚未成立時仍只能走目前地點已 authored、
且自身條件成立的一跳出口。

跑：

```bash
uv run pytest tests/
```

## Brain in headless

預設 headless 模式用 `EchoBrain`（NPC 對話永遠回固定一句），
所以同樣的腳本每次跑都一樣 — 對 CI / regression test 非常重要。
要用真 Claude，把 `ANTHROPIC_API_KEY` 設好、跑 CLI 而不是 HeadlessSession，
或在程式裡顯式傳 `brain=ClaudeBrain()`。

## Screenshot mode（半 headless）

`--screenshot out.png` 是「跑一個帶 surface 的 App，但用 SDL dummy driver
不開實際視窗」的折衷模式 — 對 AI agent 驗證 UI 渲染特別好用：

```bash
uv run world-gal-game --pack demo_pack \
    --screenshot screenshots/starting_room.png \
    --autoplay 1.0 \
    --dev-start explore \
    --dev-location starting_room
```

`--dev-start` 可選值：
- `explore` / `map` / `affection` / `log` / `save` / `load`
- `settings` / `menu` / `achievements` / `inventory`
- `scene:<id>` — 直接開某個場景
- `chat:<npc_id>` — 開自由對話

`--dev-start map` 會直接繪製主要的 `MapScene`（包含 pack-authored
`travel.spatial_map` 背景與校外縮圖），不會改成目的地卡片列表。

`--dev-flags` / `--dev-affection` / `--dev-location` / `--dev-time`
可以預設玩家狀態。
