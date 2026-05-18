# World Gal-Game

一個用 **pygame** 寫的 Gal-Game 引擎框架。引擎本身不綁定任何一款遊戲；
每款遊戲都是一個獨立的內容包（pack），可以放在：

- `games/<pack>/`（隨引擎一起發佈的內建 pack）
- 引擎倉庫的兄弟資料夾 `../<pack>/`（給長期維護、有自己 git 倉的遊戲用）
- `~/.world-gal-game/packs/<pack>/`（使用者本地的 pack 緩存）
- 任何由 `--pack <path>` 指定的絕對 / 相對路徑

本 repo 內附 `games/demo_pack/`——一個 5–10 分鐘可玩完的範例遊戲，
用來示範引擎大部分子系統（對白、任務、商店、物品、好感度、成就、多時段背景）。
外部 game pack 可以放在 sibling 資料夾 `../<pack>/`、`~/.world-gal-game/packs/<pack>/`，
或用 `--pack <path>` 直接指定路徑。

## 完整開發者文檔

完整的 pack 開發指南、effect / condition 參考、cookbook 都在
[`docs/`](docs/README.md) 目錄下：

| 文件 | 主題 |
|---|---|
| [getting-started](docs/getting-started.md) | 安裝、產生第一個 pack、跑起來 |
| [pack-format](docs/pack-format.md) | pack 目錄結構 + meta.yaml 完整欄位 |
| [scenes](docs/scenes.md) | 場景 YAML、對白、選項、條件 |
| [characters](docs/characters.md) | 角色、立繪、送禮、商店 |
| [affection](docs/affection.md) | 好感度、多軸 stat、門檻 |
| [resources](docs/resources.md) | 金錢 / 體力 / 學分等自訂資源 |
| [items](docs/items.md) | 物品、消耗、送禮 |
| [shops](docs/shops.md) | 商店、買賣 |
| [achievements](docs/achievements.md) | 成就（含隱藏成就） |
| [theme-and-locale](docs/theme-and-locale.md) | 換配色 / 換語系 |
| [headless](docs/headless.md) | 無視窗 inspect + script |
| [effects-reference](docs/effects-reference.md) | 全 effect kind 參考 |
| [conditions-reference](docs/conditions-reference.md) | 全 condition kind 參考 |
| [cookbook](docs/cookbook.md) | 常見模式（多結局、體力歸零強制睡覺、送禮…） |
| [architecture](docs/architecture.md) | 引擎內部結構（給想擴充引擎核心的人） |


## 引擎能做什麼

- **RPG-Maker 風格地圖**：節點式地點、時段切換、可進入條件、NPC 出沒時間表
- **多軸好感度**：每個角色可同時追蹤多個 stat（例如 affection / trust / fear），跨門檻自動解鎖內容
- **分支劇情**：YAML 撰寫的場景，條件式選項，跨場景轉場、`on_end` 自動鏈接結局
- **事件記錄 + Flags**：玩家每個選擇、地點、對話都會寫入 timeline，可被條件查詢
- **成就系統**：宣告式 YAML 設定 requires/forbids，達成時自動觸發、UI 自動掉 toast
- **物品 + 送禮**：根據角色 likes/dislikes 自動算好感度增減
- **存讀檔**：多存檔槽，使用者目錄持久化（PyInstaller 打包後仍可寫入）
- **VN 對話 scrollback**：滾輪上推或按 B 看過去所有對話
- **完整在地化 / 主題**：每個 pack 可在 `meta.yaml` 蓋掉預設配色、字型、UI 文字、好感度等級命名
- **截圖 + Headless 模式**：可從命令列輸出畫面 PNG，也可完全無視窗驅動引擎驗證劇情邏輯
- **PyInstaller 打包**：一行指令把引擎 + 任何 pack 打成 .app / .exe


## 快速試玩內附 demo pack

```bash
uv venv
uv pip install -e .

# 跑內附的範例 pack（demo_pack）
uv run python main.py

# 看可用的 pack
uv run python main.py --list-packs

# 切到別的 pack
uv run python main.py --pack my_game
```


## 用引擎做你自己的遊戲

### 一行指令產生新 pack skeleton

```bash
uv run python tools/scaffold_pack.py --pack my_game --title "我的遊戲"
uv run python main.py --pack my_game
```

產生出來的目錄長這樣：

```
games/my_game/
├── content/
│   ├── meta.yaml          # 標題、起始地點/場景、theme/locale 覆寫
│   ├── locations.yaml     # 地點 + NPC 出沒 + scene_hooks
│   ├── characters.yaml    # NPC + 好感度門檻 + 商店
│   ├── items.yaml         # （選填）可送的物品
│   ├── achievements.yaml  # （選填）成就
│   └── scenes/            # 多檔散裝；全部會被自動載入
└── assets/
    ├── backgrounds/
    ├── characters/
    ├── cgs/
    ├── ui/
    ├── fonts/
    └── bgm/
```

YAML 內的資產路徑全部用 `assets/...`（pack-relative），所以整個 pack
目錄可以原封不動搬到別的 repo。

### meta.yaml 可以蓋掉哪些引擎預設

```yaml
title: "我的遊戲"
subtitle: "副標題"
text_speed: 60                # 文字顯示速度（chars/sec, 0 = 瞬間）
start_location: starting_room
intro_scene: prologue
title_bg: assets/backgrounds/title.png
bundled_font: assets/fonts/MyFont.ttf

# 整套配色（不寫的鍵保留引擎預設）
theme:
  accent:      [200, 100, 200]
  accent_alt:  [120, 120, 240]
  accent_warm: [240, 220, 120]

# 完整在地化
locale:
  affection_levels:
    - {min: 0,   label: "Stranger"}
    - {min: 25,  label: "Friend"}
    - {min: 50,  label: "Close"}
    - {min: 100, label: "Lover"}
  time_of_day:
    morning: "Morning"
    midnight: "Witching Hour"
  ui:
    new_game: "New Game"
    map: "Map"

# 玩家起始持有物
starting_inventory:
  jasmine_tea: 1
```

### Scene YAML（最小例）

```yaml
scenes:
  - id: meet_someone
    title: "初遇 · 某某"
    location: library
    background: assets/backgrounds/library.png
    bgm: assets/bgm/library.ogg
    lines:
      - text: "雨剛停，圖書館的窗外風很安靜。"
      - speaker: "某某"
        text: "「你也喜歡這本書？」"
        portrait: assets/characters/someone_smile.png
        expression: smile
      # llm_speaker 欄位保留給未來的 LLM 重接（v2）；v1 永遠顯示 text。
      - speaker: "某某"
        text: "「對不起，我嚇到了。」"
    choices:
      - id: friendly
        text: "「下次我請你喝咖啡？」"
        requires:
          - {kind: affection_gte, target: someone, value: 10}
        effects:
          - {kind: affection, target: someone, value: 5}
          - {kind: set_flag, target: had_coffee_promise}
        next_scene: someone_route_1
      - id: leave
        text: "「我先走了。」"
        effects: ["affection:someone=-2"]   # 也支援字串簡寫
```

### 支援的 effect 與 condition

**Effect kind**:
- 狀態：`affection · stat · set_flag · increment_flag`
- 流動：`advance_time · move_to · unlock_location · play_scene · end_scene`
- 記錄：`log_event`
- 物品：`give_item · take_item · gift`（消耗物品送給 NPC，自動套用好感度）

**Condition kind**:
- 狀態：`flag · not_flag · flag_eq · affection_gte · affection_lt`
- 進度：`time_in · visited · scene_played · has_item · achievement`

### 成就 YAML

```yaml
achievements:
  - id: ach_first_step
    title: "新生入學"
    description: "踏出宿舍房間，正式開始校園生活。"
    requires:
      - {kind: flag, target: orientation_done}

  - id: ach_secret_ending
    title: "舊書與晚風"
    description: "走到了真正的結局。"
    hidden: true        # 解鎖前在成就頁顯示為 ???
    requires:
      - {kind: flag, target: ending_secret}
```

引擎在每次套用 effect 後自動評估全部成就，新解鎖的會：
1. 寫入事件記錄（kind = "unlock"）
2. 觸發右上角的 toast 通知
3. 出現在「成就」overlay 頁

### 物品 / 送禮

```yaml
items:
  - id: jasmine_tea
    name: "茉莉花茶"
    description: "她最喜歡的味道。"
    matches_tags: ["茉莉花茶"]   # 對應 character.likes 的標籤

  - id: instant_camera
    name: "拍立得相機"
    matches_tags: ["拍照"]       # 命中 character.dislikes
```

送禮邏輯：item 的 `matches_tags` 命中 NPC 的 `likes` → 好感 +8；命中 `dislikes` → -5；
都不命中 → +2（送禮這個動作本身就有意義）。要徹底自定義可用
`gift_modifier: {npc_id: 数字}` 蓋過去。

YAML 內呼叫：

```yaml
choices:
  - id: give_tea
    text: "「我帶了一罐妳會喜歡的茶。」"
    requires:
      - {kind: has_item, target: jasmine_tea}
    effects:
      - {kind: gift, target: someone, stat: jasmine_tea}
```


## LLM NPC 大腦（v2 deferred）

LLM 接入已 deferred 到 v2。`Brain` 介面與相關 YAML 欄位（`llm_brain` /
`llm_speaker` / `llm_directive`）仍保留，方便未來重接 ClaudeBrain 時
**完全不用改 game pack**。v1 的劇本：

- `llm_speaker: true` 的 line 一律顯示 `text:` fallback
- 點 NPC card 開出 NPC 行動 overlay（送禮 / 看貨），沒有自由對話

## 引擎內部架構

```
engine/
├── config.py              # 螢幕大小、字型偵測、路徑（含 PyInstaller bundle 對應）
├── app.py                 # pygame 主迴圈 (GalGameApp)
├── content_loader.py      # 載入 YAML 內容包
├── headless.py            # 無視窗驅動 + 腳本化遊玩
├── pack_registry.py       # 掃 games/ 目錄列出可用 pack
├── core/                  # 純 pydantic 資料模型（UI 無關）
│   ├── game_state.py      # 整個遊戲狀態根
│   ├── affection.py       # 多軸好感度
│   ├── event_log.py       # 事件記錄 + flags + DialogueHistory
│   ├── story_graph.py     # Scene / Line / Choice / Condition / Effect
│   ├── map_system.py      # 地點 / NPC 出沒 / scene_hooks
│   ├── time_system.py     # 第 N 天 + 時段
│   ├── achievements.py    # 成就 tracker
│   ├── inventory.py       # 物品 + 送禮邏輯
│   ├── localization.py    # i18n labels (pack 可覆寫)
│   └── save_manager.py    # JSON 存讀檔
├── dialogue/              # 對話引擎
│   ├── dialogue_engine.py # Scene 跑者
│   └── script_loader.py   # YAML → Scene/Line/Choice
├── npc/                   # NPC（LLM brain v2 deferred）
│   ├── npc_base.py        # NPC 類別 + 短期記憶
│   └── llm_brain.py       # LLMBrain ABC + EchoBrain（v2 預留 seam）
├── ui/                    # pygame UI 元件
│   ├── assets.py          # 圖檔/聲音快取（含 placeholder fallback）
│   ├── fonts.py           # CJK 字型偵測 + 快取
│   ├── theme.py           # 配色、間距、圓角（pack 可從 meta 覆寫）
│   ├── input.py           # 每幀輸入快照
│   ├── transitions.py     # fade in/out
│   └── widgets/           # Panel, Button, Label, DialogueBox, MapView, Toast, ScrollArea...
└── scenes/                # 螢幕狀態機（pygame Scene = 一個畫面）
    ├── base.py            # Scene + SceneManager
    ├── title.py
    ├── exploration.py     # 地點探索（NPC + 行動按鈕）
    ├── dialogue_scene.py
    ├── map_scene.py
    ├── affection_scene.py
    ├── event_log_scene.py
    ├── achievements_scene.py
    ├── inventory_scene.py
    ├── scrollback_scene.py
    ├── settings_scene.py
    ├── save_scene.py
    └── npc_action_scene.py # NPC 行動 overlay（送禮 / 看貨）
```


## Headless 模式（給 CI / AI agent 用）

Headless 模式不需要顯示器或音效裝置，是給自動化開發者「看見」遊戲狀態用的：

```bash
# 把目前遊戲狀態（地點、好感度、可用場景…）以 JSON 輸出
uv run python main.py --headless --inspect

# 跑一個指令腳本，然後輸出最終狀態
uv run python main.py --pack demo_pack --headless \
    --script games/demo_pack/scripts/test_lover_route.json
```

腳本格式：

```json
{
  "commands": [
    {"op": "start_scene", "scene": "prologue"},
    {"op": "next", "count": 8},
    {"op": "move", "location": "town_square"},
    {"op": "start_scene", "scene": "meet_heroine"},
    {"op": "choose", "choice": "accept_quest"},
    {"op": "adjust_affection", "npc": "heroine_1", "delta": 75},
    {"op": "set_flag", "key": "secret", "value": true},
    {"op": "inspect"}
  ]
}
```

支援的 op：`start_scene · next · choose · move · advance_time ·
set_flag · adjust_affection · inspect`（`chat` op 在 LLM 重接後再啟用）


## 截圖模式

```bash
# 標題畫面截圖
uv run python main.py --screenshot out/title.png --autoplay 1.0

# 直接進入遊戲 + 停在指定狀態截圖
uv run python main.py --screenshot out/explore.png --autoplay 1.0 \
    --dev-start explore --dev-location library

# 截任何特定 overlay
uv run python main.py --screenshot out/map.png --autoplay 1.0 --dev-start map
uv run python main.py --screenshot out/ach.png --autoplay 1.0 \
    --dev-start achievements --dev-flags '{"orientation_done": true}'
```

`--dev-start` 可選值：
- `explore`、`map`、`affection`、`log`、`save`、`load`、`settings`、`achievements`
- `scene:<scene_id>` — 開始播某個場景

額外調整：
- `--dev-flags '{...}'` — 預先 set flags
- `--dev-affection '{"npc": 50}'` — 預先給好感度
- `--dev-location <id>` — 預先移動到某個地點
- `--dev-time morning|noon|afternoon|evening|night|midnight`


## 測試

```bash
uv pip install pytest
uv run pytest tests/                # 25 個 unit test
uv run python scripts/run_smoke.py  # 端到端通關測試
```

涵蓋好感、事件記錄、條件/效果、地圖、對話引擎、成就、物品送禮、demo_pack 三條主線（alone / friend / lover）。
任何引擎改動都應該守住這兩個。


## 打包成 .exe / .app

```bash
uv pip install pyinstaller
uv run python build.py
# 結果 → dist/<game-name>/
```

`build.spec` 會把整棵 `games/` 都包進去，使用者下載解壓後直接執行。


## 開發小技巧

### 鍵盤快捷鍵

| 鍵 | 動作 |
|---|---|
| Space / Enter / Z | 推進對話 |
| 按住 Ctrl | 快進 |
| Esc / X | 關掉最上層 overlay |
| 滾輪上 / B | 開對話 scrollback |
| M / A / L / T / I / S | 地圖 / 好感 / 事件 / 成就 / 物品 / 存檔 |
| F12 | 截圖（存到使用者目錄 / screenshots/） |
| F11 | 在 stderr 印出當前狀態 JSON（debug） |


## 授權

Engine is MIT-licensed. External game packs ship with their own licenses;
check each pack's own README before redistributing its scripts or art.
