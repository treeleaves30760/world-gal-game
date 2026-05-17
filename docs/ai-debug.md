# AI Debug Driver

`wgg debug` 把整個遊戲 boot 在無視窗模式，讓 AI agent / test 程式可以
注入點擊與鍵盤事件、抓螢幕截圖、傾印狀態快照。

設計目標是「讓 AI 自己驗證 bug」— 不用人類玩家手動操作就能 reproduce
與診斷遊戲問題。

## 兩種使用方式

### 1. CLI（適合 agent 與 CI）

寫一個 JSON script，丟給 `wgg debug`：

```bash
cat > /tmp/repro.json <<'EOF'
{
  "pack": "tsing_hua_strange_tales",
  "actions": [
    {"do": "new_game"},
    {"do": "skip_dialogue", "max_frames": 800},
    {"do": "snapshot", "path": "before.json"},
    {"do": "screenshot", "path": "before.png"},
    {"do": "click_label", "label": "校門口", "after": 20},
    {"do": "snapshot", "path": "after.json"},
    {"do": "screenshot", "path": "after.png"}
  ]
}
EOF

uv run world-gal-game debug /tmp/repro.json --out-dir /tmp/debug_out
```

`--out-dir` 內會有：

- `before.png`、`after.png` 螢幕截圖
- `before.json`、`after.json` 該時刻的 state 快照
- `report.json` 每一步動作的執行結果（含錯誤訊息）

### 2. Python（適合自動化測試）

```python
from world_gal_game.dev.driver import GameDriver

d = GameDriver(pack="my_pack")
d.new_game()
d.skip_dialogue()

# Find a button by label substring, click its center
btn = d.find_widget(label="校門口")
d.click(btn["rect_center"])
d.advance_frames(20)

# Verify the move worked
assert d.snapshot()["location"] == "main_gate"
d.screenshot("after.png")
d.quit()
```

## 可用的 actions（CLI script）

| op | 參數 | 動作 |
|---|---|---|
| `new_game` | — | 直接從 TitleScene 走 New Game flow |
| `skip_dialogue` | `max_frames` | 連按 Space 直到 ExplorationScene |
| `frames` | `n` | 跑 N 個遊戲 frame |
| `space` | `count`, `between` | 按 Space N 次（間隔 frames） |
| `key` | `key` (e.g. `"escape"`, `"f1"`), `after` | 按一個鍵 |
| `click` | `at: [x, y]`, `after` | 滑鼠左鍵點某座標 |
| `click_label` | `label`, `after` | 找含此 label 子字串的 button 並點它的 center |
| `find` | `label`, `path` | 列出符合的 widget（不點） |
| `set_flag` | `key`, `value` | 直接寫 game state 旗標（跳過劇情用） |
| `screenshot` | `path` | 寫 PNG。相對路徑 = out_dir 內 |
| `snapshot` | `path`(optional) | 抓 state JSON。若有 path 也寫檔 |

`after`（預設 4 frames）= 動作後額外推進的 frame 數，給動畫 / scene push 時間落定。

## Snapshot 內容

每個 `snapshot` 結果含：

```json
{
  "pack": "tsing_hua_strange_tales",
  "scene_top": "ExplorationScene",
  "scene_stack": [{"scene": "TitleScene"}, {"scene": "ExplorationScene"}],
  "location": "player_dorm",
  "location_name": "自宅 · 學齋宿舍",
  "time": "第 1 天 · 週一 · 早晨",
  "time_of_day": "morning",
  "player_name": "玩家",
  "flags": {"intro_done": true, ...},
  "affection": {"qingyi": 0, "yuening": 0, ...},
  "resources": {"money": 500, "energy": 100},
  "inventory": {"jasmine_tea": 1},
  "achievements_unlocked": [],
  "quests_active": [],
  "quests_completed": [],
  "current_scene_id": null,
  "current_line_index": 0,
  "widgets": [
    {
      "path": "_exit_buttons[0][0]",
      "label": "→ 校門口",
      "enabled": true,
      "visible": true,
      "has_on_click": true,
      "rect": [624, 586, 200, 42],
      "rect_center": [724, 607],
      "style": "primary"
    },
    ...
  ]
}
```

`widgets` 是當前最上層 scene 的所有可點按鈕清單 — `path` 標示來源屬性，
`rect_center` 可直接餵給 `click` 或 `click_label`。

## 跟一般 headless 模式的差別

| | `wgg --headless --inspect` | `wgg debug` |
|---|---|---|
| 用途 | 一次性 JSON dump | 多步驟驅動 |
| 輸入注入 | scripted commands（高階） | 真實 pygame events（低階） |
| UI 互動 | 不模擬 widget click | 可點 widget |
| 螢幕截圖 | 否 | 是 |
| 適合場景 | CI 通關驗證 | 視覺 bug 重現、widget 行為測試 |

兩者互補：`--headless --inspect` 適合「整條路線跑完最終 state 對嗎」，
`wgg debug` 適合「**這個按鈕到底有沒有反應**」「**畫面長這樣對嗎**」。

## 寫一份典型的 bug repro

> 「點 X 按鈕沒有反應」

```json
{
  "actions": [
    {"do": "new_game"},
    {"do": "skip_dialogue"},
    {"do": "find", "label": "X"},
    {"do": "snapshot", "path": "before.json"},
    {"do": "click_label", "label": "X"},
    {"do": "snapshot", "path": "after.json"},
    {"do": "screenshot", "path": "after.png"}
  ]
}
```

跑完後對比 `before.json` 跟 `after.json` 的 `flags` / `location` /
`scene_top` / `current_line_index`，**state 變化就是按鈕的作用**。
無變化 = bug 確實存在；有變化 = bug 在別處（可能 render 或 toast）。

## 給 AI agent 的 prompt 範例

> 「我在懷疑 Foo 按鈕沒反應。請寫一個 wgg debug script 通過 click 後
> 對比 snapshot，如果 state 真的沒變化就到 exploration.py 找這個按鈕
> 的 on_click handler，回報 root cause。」
