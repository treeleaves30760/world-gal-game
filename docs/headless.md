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
  "player": {"name": "玩家", "pronouns": "他"},
  "time": {"day": 1, "weekday": "mon", "time_of_day": "morning", ...},
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
| `start_scene` | `scene` | 開始一個場景 |
| `next` | `count` (預設 1) | 推進 N 行對白；遇到 choice/end 早退 |
| `choose` | `choice` | 選一個選項 id |
| `chat` | `npc`, `message` | 對 NPC 自由對話一次 |
| `move` | `location` | 移動到地點（會觸發 time +1 phase） |
| `advance_time` | `phases` (預設 1) | 推進 N 個時段 |
| `set_flag` | `key`, `value` (預設 true) | 設旗標；會觸發成就重評 |
| `adjust_affection` | `npc`, `delta`, `stat` (預設 "affection") | 調好感 |

未來會補：`gain_resource` / `spend_resource` / `give_item` / `use_item` /
`buy_item` 等。目前可以用 `start_scene` 跑一個小場景間接呼叫。

## 自動跟著場景轉場

`next_line()` 和 `choose()` 如果遇到 `on_end: play_scene` 觸發的場景連接，
會**自動繼續播下一個場景的第一行**。所以一條主線通常一個 script 就跑得完。

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

`--dev-flags` / `--dev-affection` / `--dev-location` / `--dev-time`
可以預設玩家狀態。
