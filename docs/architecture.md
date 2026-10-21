# Architecture

寫這份是給「想擴充引擎核心」的人 — 加新 effect kind、改 UI、寫新 widget。
只寫 game pack 的話這份可以略過。

## 模組分層

```
world_gal_game/
├── __init__.py              # 對外 API: run(), headless_run(), EngineConfig
├── cli.py                   # console script entry
├── config.py                # EngineConfig + pack 路徑解析
├── content_loader.py        # YAML → GameState
├── headless.py              # 無視窗驅動 + 腳本執行
├── pack_registry.py         # 掃 games/、sibling、user-cache 找 pack
├── app.py                   # GalGameApp（pygame 主迴圈）
│
├── core/                    # 純 pydantic — UI 無關
│   ├── game_state.py        # 整個遊戲狀態根；evaluate() + apply()
│   ├── affection.py         # 多軸好感 + 門檻
│   ├── event_log.py         # 事件記錄 + flags + DialogueHistory
│   ├── story_graph.py       # Scene / Line / Choice / Condition / Effect
│   ├── map_system.py        # 地點 / NPC 出沒 / scene_hooks
│   ├── time_system.py       # 第 N 天 + 時段
│   ├── achievements.py      # 成就 tracker
│   ├── music_room.py        # canonical 已聽曲目進度（save / ClearData identity）
│   ├── music_room_spec.py   # 選填 music_room.yaml 的靜態產品 metadata
│   ├── inventory.py         # 物品 + 送禮邏輯
│   ├── resources.py         # 通用具名整數資源
│   ├── shop.py              # Shop + ShopListing
│   ├── localization.py      # i18n labels
│   └── save_manager.py      # JSON 存讀檔
│
├── dialogue/                # 對話引擎
│   ├── dialogue_engine.py   # Scene 跑者（line-by-line）
│   └── script_loader.py     # YAML → Scene/Line/Choice
│
├── npc/                     # NPC（LLM brain v2 deferred）
│   ├── npc_base.py          # NPC + memory + system_prompt
│   └── llm_brain.py         # LLMBrain ABC + EchoBrain（為 v2 預留 seam）
│
├── plugins/                 # 插件系統（Phase 1）— 詳見 docs/plugins.md
│   ├── __init__.py          # 公開 decorator + 內建 bootstrap
│   ├── registry.py          # EFFECT/CONDITION/HOOK/INSPECT_FIELD 全域 registry
│   ├── manager.py           # PluginManager（掃描 + 拓樸排序 + lifecycle）
│   ├── manifest.py          # PluginManifest pydantic + semver matcher
│   ├── context.py           # PluginContext + HookEvent 常數
│   ├── errors.py            # PluginError 家族 + isolate() context manager
│   ├── builtin_effects.py   # 23 個 builtin effect kind handler
│   └── builtin_conditions.py# 16 個 builtin condition kind handler
│
├── ui/                      # pygame UI 元件
│   ├── assets.py            # 圖檔 / 聲音快取（含 placeholder）
│   ├── fonts.py             # CJK 字型偵測 + 快取
│   ├── theme.py             # 配色、間距、圓角
│   ├── input.py             # 每幀輸入快照
│   ├── transitions.py       # fade in/out
│   └── widgets/             # Panel, Button, DialogueBox, MapView, Toast, ...
│
└── scenes/                  # 螢幕狀態機
    ├── base.py              # Scene + SceneManager
    ├── title.py             # 標題畫面
    ├── exploration.py       # 探索（時間 + 選單按鈕 + 資源列）
    ├── dialogue_scene.py    # 對白 / 選項演出
    ├── map_scene.py         # 地圖 overlay
    ├── affection_scene.py   # 好感 overlay
    ├── event_log_scene.py   # 事件 overlay
    ├── achievements_scene.py
    ├── inventory_scene.py
    ├── scrollback_scene.py
    ├── settings_scene.py
    ├── music_room_scene.py  # metadata-driven、全語意輸入的 BGM 鑑賞
    ├── shop_scene.py
    ├── save_scene.py
    ├── npc_action_scene.py  # NPC 行動 overlay（送禮 / 看貨）
    └── menu_scene.py        # 主選單（集中 UI）
```

## 資料流

```
content/*.yaml          [load]
       │
       ▼
content_loader.load_pack()
       │  builds:
       ▼
GameState
├── PlayerInfo
├── AffectionTracker         # per-NPC stats
├── EventLog + DialogueHistory + flags
├── MapSystem                # locations + current + visited
├── StoryGraph               # scenes catalogue
├── TimeSystem               # day / weekday / phase
├── AchievementTracker
├── ItemRegistry + Inventory
├── ResourceTracker          # 通用整數
└── meta: dict               # private bridges; e.g. __npc_registry__

NPCRegistry                  # parallel registry of NPCs
```

`GameState.evaluate(Condition)` 與 `GameState.apply(Effect)` 是
*所有* 場景邏輯的中央分派 — 任何狀態變更都流經它。Phase 1 起，內部走的是
plugin registry 查表：

```
state.apply(Effect(kind="affection", ...))
   └─ EFFECT_REGISTRY.get("affection") → EffectEntry(fn=handle_affection, plugin_id="builtin")
      └─ entry.fn(state, eff)
```

39 個內建 kind 被 `plugins/builtin_effects.py` + `builtin_conditions.py` 註冊。
第三方插件透過 `@effect("kind")` 加入同一份 registry — 沒有 if-elif 差別對待。
詳見 [plugins.md](plugins.md)。

## App 流程

```
GalGameApp.__init__
  ├── pygame.init()
  ├── load_pack() → state, npcs, meta
  ├── 套用 meta 到 EngineConfig（title / text_speed / ...）
  ├── 構造 UI services: AssetManager(pack_root), FontRegistry, Theme.from_meta
  ├── 構造 Localization.from_meta + bind 到 affection / time_system
  ├── 構造 DialogueEngine（用 EchoBrain or ClaudeBrain）
  ├── 構造 SceneContext（彙整以上所有，含共享 live MotionPolicy）
  └── 把 TitleScene 推上 SceneManager

App.run() 主迴圈：
  ├── 每幀：
  │     ├── pygame.event.get() → persistent InputRouter.collect()
  │     │                         → per-frame InputState
  │     ├── manager.update(dt, inp)
  │     ├── _poll_achievement_toasts() ← apply_all 推進的事件 / 物品 / 資源 deltas
  │     ├── toast_stack.update / draw
  │     └── pygame.display.flip()
  └── 偵測 inp.quit_requested 退出
```

## 資產生命週期與記憶體預算

`ui/assets.py::AssetManager` 以「解碼後占用」而不是壓縮檔大小管理常駐資產：

- 圖像、縮放圖與無標籤 placeholder 共用一個 byte-bounded LRU。Surface 以
  `pitch × height` 計費，預設 256 MiB；單張超過預算仍可顯示，但不會污染 cache。
- 短 SFX／voice／ambient 共用另一個 decoded-PCM LRU，依 mixer 頻率、sample
  format、聲道數與時長估算，預設 64 MiB。
- BGM 不進短音效 cache。雙 channel crossfade 最多保留目前／淡出的兩個
  `Sound`；同一曲可跨 slot 重用，立即停止時會釋放兩個 slot，避免長流程把所有
  曾播放曲目都解碼常駐。
- 切換 pack root 會先停止 live audio，再清掉所有路徑型 cache，避免相同 authored
  path 顯示上一個 pack 的像素或播放上一個 pack 的 PCM。
- `cache_stats()` 提供 image／short sound 的 entry、bytes、budget，以及 BGM、
  ambient、voice、去重後總 retained-audio bytes，供 debug 與長跑 QA 取樣。
- pygame 的 `Channel` 沒有跨 backend 一致的 seek／position API。Voice save checkpoint
  因此以 injected monotonic clock 保存 logical source offset；讀檔時依 mixer frequency、
  sample format 與 channels 將 `Sound.get_raw()` 切在完整 PCM frame，並只保留正在播放的
  tail `Sound`。Checkpoint v2 同時保存 decoded PCM 的 SHA-256 與 mixer format；內容遭
  同長度替換、格式不同、PCM 不可取得或 mixer 失敗時採明確 silent fallback。內層 v1
  checkpoint 因沒有素材身份也保持安靜；只有完全缺少 `voice_playback` 欄位的舊外層
  resume 才維持從 sample 0 重播的相容行為。
- 圖片 decode／scale 的 `OSError`、`MemoryError` 與 `pygame.error` 在 asset boundary
  降級成目的尺寸 placeholder；短音效／voice decode 失敗則回傳 silent、也不寫入 PCM
  cache。Exact voice restore 會留下可檢查的 silent outcome。這個邊界只攔資產失敗；
  `KeyboardInterrupt`／`SystemExit` 不會被吞，fallback 本身配置失敗也仍可向上拋出。

`EngineConfig.max_image_cache_mb`（預設 256）和
`EngineConfig.max_sound_cache_mb`（預設 64）是 process/runtime tuning，不寫入玩家
`settings.json`。這兩個上限只約束可重建 cache；正在顯示的 scene Surface、pygame
channel 仍在播放的資料與 renderer/plugin 自有 cache 必須在整體記憶體量測中另外
計入。

## Scene Manager

```
SceneManager
└── stack: list[Scene]    # 顯示時：找最後一個 is_overlay=False 的，從那裡往上 draw
    ├── ExplorationScene             ← bottom (full screen)
    ├── MenuScene (is_overlay=True)
    └── InventoryScene (is_overlay=True, pushed from menu)  ← top, receives input
```

- `push(scene)` 疊一個 overlay
- `pop()` 拿掉最上層
- `replace(scene)` 換掉最上層
- `clear_to(scene)` 清空所有後推進這個（從探索回標題用）

scene 之間的通訊只透過 `Scene.enter(**kwargs)` 拿回呼。App 是中央
hub，由它組裝這些 callback。

`SceneContext.motion` 是 engine-wide 的動態無障礙權威。它即時讀取
`EngineConfig.reduce_motion`，並由 SceneManager、所有共用 scene/widget、Toast、
portrait backend 與 ambient backend 共用；元件不可在建構時複製一份設定 bool。
政策把動態分成 feedback、opacity、spatial、flash、continuous 與 content：減動時
必要狀態回饋立即就位、opacity 最多 0.12 秒、位移／閃光／持續裝飾停止，而 typewriter、
語音與影片等敘事內容繼續。App 初始化後也把同一物件綁到
`PluginContext.motion`，供 runtime hooks 查詢；早期 pack-load hooks 則允許為
`None`。外掛 renderer 仍只負責繪製，不能繞過這個引擎邊界。

## 章節系統（Chapters）

多路線、多章節的長篇 VN 想把「章 / 幕 / 路線」當成一等結構來推理，而不是
只看單一 scene。章節系統把這層做成**可選的宣告式 overlay**，是純結構元資料，
**不改 runtime dispatch**：沒有 `chapters.yaml` 的 pack 完全不受影響。

**元資料模型**（`core/chapter_spec.py`）

- `ChapterSpec`：一個宣告的章節 —
  `id` / `title` / `subtitle`（標題卡下方的人讀副標，**選填**）/ `route`（路線標籤）/
  `act`（更高層的幕 / 學年分組，例 `y1`…`y4`）/ `order`（敘事排序鍵）/
  `entry_scene` / `scenes`（成員 scene id）/ `endings`（這條路線可達的結局）。
- `ChapterManifest`：整包的章節結構，附 `ordered()` / `by_route()` /
  `scene_to_chapter()` 等查詢。
- 來源 `content/chapters.yaml`（像 `variables.yaml` 一樣載入），由
  `content_loader` 停在私有橋接 `state.meta["__chapters__"]`（save 時被
  `SaveManager` 過濾掉）。`world-gal-game chapters <pack>` 可檢視 / 交叉檢查。

**runtime 欄位**

- `GameState.current_chapter: str | None` — 玩家目前所在章節的游標（預設
  `None`）。這是章節系統**唯一**的 runtime 狀態，會進存檔。

**effects**（`plugins/builtin_effects.py`，走中央 `apply` dispatch）

- `set_chapter`：把游標設到某 chapter id（須存在於 manifest）。
- `advance_chapter`：移到 `ordered()` 的下一章（`None` → 第一章）。
- 兩者都會發 `chapter.change` hook，並（除非 `value: false`）排入一個
  `chapter_card` 視覺指令。未知章節 / 沒有 manifest 時回 `{"error": ...}`
  降級，不會 crash（isolate 契約）。

**conditions**（`plugins/builtin_conditions.py`）

- `in_chapter`：目前章節是否在給定 id 之一。
- `chapter_at_or_after`：目前章節的 `order` 是否 ≥ 目標章節的 `order`
  （拿來閘門「到了第幾章之後」的內容）。

**hook**

- `HookEvent.CHAPTER_CHANGE`（`"chapter.change"`）：`current_chapter` 變動時
  觸發，帶 `chapter` / `previous` / `title` / `route` / `order`。

**標題卡指令流**（eyecatch）

```
set_chapter / advance_chapter
  → _queue_visual_fx({"fx": "chapter_card", title, subtitle})   # 排入 visual-fx 佇列
  → DialogueScene._spawn_visual_fx 認得 chapter_card → on_chapter_card(directive)
  → App._open_chapter_card 推一個 ChapterCardScene overlay（不透明 eyecatch）
  → 點擊 / 按鍵 / 自動逾時後 pop 回對話
```

`subtitle` 只用 pack 作者寫的 `ChapterSpec.subtitle`，**不會**退回 `route`/`act`
標籤（那讀起來是機器術語）。

**章節選單 / 流程圖**

`FlowchartScene`（`scenes/flowchart_scene.py`）把 manifest 畫成依
**幕 / 學年（act）分列**的分支流程圖：共通線走頂列，路線只在分歧處才branch
進自己的lane；標題自動折成兩行並可橫向捲動。已讀章節（依
`state.read_log.scenes`）會亮起且可點擊跳轉（`on_jump`，暫停選單用）；標題畫面
的瀏覽模式 `on_jump=None`，純看不跳。

**目前限制（已知未來增強）**

章節目前是**描述性 + effect 驅動**：引擎**不會**自動執行一張章節圖
（不會「跑到下一章就自動播下一章的 scene」）。作者要自己用 effects / hooks
把流程串起來（例如某 scene 的 `on_end` 放 `advance_chapter`，或在
`chapter.change` hook 裡 `play_scene`）。**「章節圖自動執行」（chapter-graph
execution）列為已知的未來增強**；屆時 manifest 的 `entry_scene` / `scenes` /
`order` 可直接驅動章節推進，作者就不必手動接線。

## CG 圖鑑的靜態／持久化分層

CG 系統刻意不把產品文案塞進存檔：

```text
content/cg_gallery.yaml
  → CGGalleryManifest                     # 靜態 roster / title / order
  → state.meta["__cg_gallery_manifest__"] # 私有 bridge，序列化時排除

scene.cg / line.cg / show_cg.target
  → canonical_cg_path                      # trim / slash / dot normalization
  → CGGallery.unlocked: set[str]           # canonical asset identity
  → save JSON + clear_data.json            # 玩家進度

CGGalleryScene
  → 以 manifest 排序與顯示 metadata
  → hidden locked 不出現
  → 未宣告但已解鎖 path 合成 legacy fallback
```

模型在 `core/cg_gallery_spec.py`；persistent tracker 仍在
`core/cg_gallery.py`。這個界線讓 pack 可在不 bump save schema、不改
`ClearData.cgs_seen` 的前提下加入正式標題、副標、描述、角色／章節關聯與縮圖。

manifest 缺省時 `CGGalleryScene` 才掃描 `assets/cgs/`；manifest 存在（包括
明確空清單）時是權威 roster，避免生成草稿或差分變體被意外當成商品內容。
`show_cg` 和 scene / line `cg` 一樣，在 effect 真正執行時以同一路徑解鎖。
舊版曾容許的空白 path 會在 save／clear-data 載入時清理。Hot reload 會保留
read log、CG／ending unlock 與時間進度，再以新 pack 內容刷新 map 與 ending
catalog baseline；正式 load 與 quickload 也會重新綁定目前 ending definitions，
並把 CG／Music Room progress 和目前 `MediaAuthority` 取交集，避免舊存檔把新增結局
藏掉、把退役內容算進完成度，或把任意路徑送進 decoder。`MediaAuthority` 只從目前
StoryGraph、CG／Music Room manifest、BGM 目錄掃描、`title_bgm` 與 pack 啟動時明確
註冊的額外 unlock 建立；
save candidate 不參與。`ClearData.record_from_state()`／`apply_to_state()` 在 CG 的寫入與
套用兩端使用相同 authority，不能成為跨周目的 path laundering 管道。

## Music Room 的靜態／持久化分層

Music Room v1 和 CG Gallery 採同一個「產品文案不是玩家進度」界線：

```text
content/music_room.yaml
  → MusicRoomManifest                      # 靜態 roster / copy / credit / order
  → state.meta["__music_room_manifest__"] # private、save-stripped bridge

scene.bgm / line.bgm / title_bgm
  → canonical_music_path                   # slash / dot normalization
  → MusicRoom.unlocked: set[str]           # canonical asset identity
  → save JSON
  → ClearData.music_seen                   # 跨周目合法進度聯集

MusicRoomScene
  → authored manifest：formal metadata + hidden policy
  → manifest 缺省：assets/bgm/ legacy discovery + filename label
  → 目前仍授權但未編目之 unlocked path：legacy fallback row
  → semantic input / visible focus / bounded virtualized scroll
```

模型在 `core/music_room_spec.py`，persistent tracker 仍在 `core/music_room.py`。
`asset` 是唯一解鎖鍵；`loc_id` 只提供
`music_room.<loc_id>.{title,artist,usage}` 的穩定 localization segment。未寫
`loc_id` 時，以 canonical asset 的 SHA-256 前綴產生 deterministic fallback，讓既有
catalog 不必 migration；pack 預期重新命名 asset 時則應明寫它，避免翻譯鍵漂移。

manifest 缺省時才以 `assets/bgm/` 第一層建立可見 roster；manifest 存在（包括
`entries: []`）時，以宣告項目決定鎖定列。`hidden` 且尚未解鎖的項目不顯示、不計入
可見總數；非 hidden 鎖定列只呈現未知狀態，不能透過 draw、status 或 localization
查詢洩漏 title／artist／usage。合法、已解鎖但未編目的 path 才會附加 filename
fallback，維持 pack 採用 catalog 前的玩家進度。

`ClearData.record_from_state()` 與 `apply_to_state()` 在 Music Room 的寫入與套用端都
接受 `authored_music_ids`，和 CG 使用同一個 `MediaAuthority` canonicalization／交集
規則；load、quickload、新周目和 hot reload 也先 rebind 再展示。因此 serialized
`MusicRoom`、舊 ClearData、manifest metadata 或磁碟上任意路徑都不能自行擴張
decoder authority。

UI 進場時擷取 BGM／ambient／voice 的 `AudioPlaybackSnapshot`，退出任一路徑只 restore
一次；播放成功後才接管其他 bus。Input 不直接判斷實體 key：`nav_*`、`page_*`、
`focus_*`、`confirm_advance`、`back` 都走 `BindingProfile`／`InputAction`，並共享滑鼠、
觸控的 enabled guard。每列與按鈕至少 44 px、焦點自動捲入 viewport、狀態不只靠
顏色；大 roster 只畫可見列，不配置 full-content backing surface。

安全邊界由 model／validator（2 MiB、4096 entries、安全相對音訊路徑、duplicate
asset／`loc_id`、精確小寫檔名）與 `MediaAuthority` 共同負責。視覺邊界則由預設
Music Room scenario 在 1280×720／1920×1080 各捕捉一次，納入 production-check 的
17-view／34-image exact matrix；baseline 必須另由人工審核者核准，不能由捕捉動作自行
promote。

相同的 authority boundary 適用於 `StoryGraph`：存檔內完整 scene 定義只是
serialization shape，不能授權下一行的 asset、effect 或額外 scene。Load 會深拷貝
目前 authored catalogue，只搬回已知 `played` id 與通過 scene／line-count 驗證的
cursor；移除的 active scene 或超界 cursor 在 pack migration 未先修正時會使候選
load 原子失敗。Dialogue history 的 voice 路徑也會依目前 authored line allowlist
清理，再交給 Scrollback 的音訊 backend。

同一規則延伸到 map 與 NPC：`rebind_authored_map_catalog()` 從目前 YAML 深拷貝
locations、exits、hooks、ambient 與呈現資料，只保留仍存在的 current／visited 進度；
存檔中已移除的位置退回目前 authored start，而明確的 pre-location `None` 仍保留。NPCRegistry
是私有 runtime bridge，從不由 save 重建；speaker、portrait path、portrait backend／args
在 restore 時都以目前 NPC catalogue 驗證。resume candidate 自己夾帶的 StoryGraph、map
或 NPC-shaped metadata 都不能擴張 asset authority。

## 加新 effect / condition kind 的兩條路徑

**Effect.kind / Condition.kind 都是開放的 `str`**（不是 `Literal[...]`）—
分派表 `core/game_state.py` 的 `apply` / `evaluate` 從
`world_gal_game.plugins.EFFECT_REGISTRY` / `CONDITION_REGISTRY` 動態查表，所以
新增 kind 有兩條路：

### 路徑 1（首選）：寫一個 plugin

絕大多數新 kind 都應該走 plugin。原因：
- 不會碰 `core/`，所以不影響其他 pack
- 自帶 manifest + signature，PackEditor / Capability Manifest 立刻能 introspect
- 適合 genre-specific 邏輯（戀愛 / 恐怖 / 養成…）

完整教學見 [plugins.md](plugins.md)。最小範本：

```python
# games/<pack>/plugins/my_thing/plugin.py
from world_gal_game.plugins import effect

@effect("my_new_kind",
        description="What this kind does",
        signature={"target": "character_id", "value": "int"})
def handle_my_new_kind(state, eff):
    state.events.record(kind="custom", title=f"...")
    return {"kind": eff.kind, "result": ...}
```

配 `plugin.yaml`：

```yaml
id: my_thing
name: My Thing
version: 0.1.0
engine_version: ">=0.1.0"
extends:
  effects:
    - kind: my_new_kind
      description: What this kind does
      signature: {target: character_id, value: int}
```

引擎啟動時自動掃描 `games/<pack>/plugins/`、`~/.world-gal-game/plugins/`、
`world_gal_game/plugins_user/` 三個位置。

### 路徑 2：擴充 builtin

只在「這個 kind 屬於 genre-agnostic engine primitive」時走這條（例如：未來引擎
新增 `gain_xp` 之類普遍適用的 kind）。

```python
# world_gal_game/plugins/builtin_effects.py
@effect("my_new_kind", plugin_id="builtin",
        description="...", signature={...})
def handle_my_new_kind(state, eff):
    ...
    return {"kind": eff.kind, ...}
```

對應測試：

```python
# tests/test_game_state.py
def test_my_new_kind():
    s = GameState()
    out = s.apply(Effect(kind="my_new_kind", target="x", value=42))
    assert out["result"] == ...
```

**不要再回去動 `Literal[...]` 與 if-elif**：那兩個結構在 Phase 1 重構掉了，
分派完全走 registry。

## 加新 widget 的範本

```python
# world_gal_game/ui/widgets/my_widget.py
from .base import Widget

class MyWidget(Widget):
    def __init__(self, rect, *, fonts, theme, on_click=None):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.on_click = on_click

    def update(self, dt, inp):
        if self.rect.collidepoint(inp.mouse_pos) and inp.mouse_clicked:
            if self.on_click:
                self.on_click()

    def draw(self, surface):
        if not self.visible:
            return
        # ... pygame draw calls ...
```

匯出在 `world_gal_game/ui/widgets/__init__.py` 的 `__all__`。

### 系統 UI chrome 的 plugin 接縫

跨 Menu、Save/Load、Settings 與 CG Gallery 的新視覺主題不能直接加入 core。
Engine 只提供三個通用元件：

- `core/ui_skin_spec.py`：嚴格的 `ui_skin.renderer/options` selector。
- `ui/system_chrome.py`：有限、可快取的 `ChromeRequest` 與故障隔離
  `SystemChromeHost`。
- `SceneContext.ui_chrome`：system scene / Panel / Button 共用的 optional
  paint service。

實際 chrome 由 pack-local `@widget` 提供，宣告
`signature.protocol: ui_chrome/v1`。Core 始終保有幾何、文字、可見焦點、
disabled 狀態、hit-test 與輸入順序；renderer 出錯或缺少 part 時使用當版
engine 的 stock fallback。這讓第三方主題能有明確身分，又不必複製存讀檔或
設定頁的互動邏輯。

v1 對外的語意識別面如下：

- `part`：`backdrop`、`panel`、`divider`、`button`、`card`、`footer`、
  `focus`。
- `screen`：`menu`、`save`、`load`、`settings`、`gallery`。
- 共用 `Button` 的 `variant` 是 `<role>:<style>`；`enabled`、`hovered`、
  `focused`、`selected`、`emphasis` 是獨立 request state，不編進 variant。
- Save/Load 的捲動列 action chip 是非 `Button` widget 的 `button/action`
  特例；存檔列、狀態列與確認層分別使用
  `card/new|slot|special`、`footer/hint|success|error`、
  `panel/confirmation` 與 `backdrop/modal`。Menu、Settings、Gallery 的
  控制提示底板使用 `footer/hint`。

逐 screen 的穩定 tuple 見
[ui_chrome/v1 request matrix](theme-and-locale.md#ui_chromev1-request-matrix)。
矩陣約束名稱與語意，不固定 request 的呼叫順序、次數、rect 或 size。v1 可
向後相容地新增 screen、part、variant、Button role/style 或有限狀態組合，
renderer 必須對未知組合回傳 `None`；移除、改名、重新解釋既有 tuple 或改變
`Button` variant 文法才使用新 protocol version。

Core-owned 文字包含所有 functional、control、status copy 與 localization：
按鈕標籤、快捷鍵提示、存讀檔狀態、錯誤與確認訊息、計數等都在 chrome 之後
由 scene/widget 繪製。Pack renderer 只能畫 chrome 與非必要的靜態裝飾微文案，
不能成為操作或狀態資訊的唯一來源。沒有 `ui_skin` 時 host 不會建立，system
scenes 使用當版 engine 的 stock fallback 行為；這項相容性不承諾跨 engine
版本、平台、字型、locale 或解析度 byte-for-byte 相同。

## 加新 scene 的範本

```python
# world_gal_game/scenes/my_scene.py
from .base import Scene, SceneContext

class MyScene(Scene):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.is_overlay = True

    def enter(self, *, on_close=None, **_):
        self.on_close = on_close
        # 建構 widgets...

    def update(self, dt, inp):
        if inp.cancel and self.on_close:
            self.on_close()
            return
        # update widgets...

    def draw(self, surface):
        # darken background if overlay
        # draw panel + widgets...

    def describe(self) -> dict:
        # for headless inspection
        return {"scene": "MyScene"}
```

接著在 `app.py` 加 `_open_my_scene()`，從 MenuScene 或哪裡 push 它。

## 序列化 / 存檔

整個 `GameState` 是 pydantic；`SaveManager.save()` 將 `model_dump()` 的結果寫進
`_schema_version: 1` JSON envelope。對話畫面另放在 top-level `_resume`：目前是
`version: 1` 的 presentation contract，voice checkpoint 與 visual timeline 有各自的
內層版本。Additive v1 欄位可缺省以讀舊檔；未知版本、非有限數值、超界 cursor 或
不一致 timeline fail closed。Pack migration 在 `_resume` 被取出前收到完整 payload，
因此可以把 scene／cursor 與 presentation 一起遷移。具有 `Line.loc_id` 的 pack 會以
「下一個尚未處理 line」或 end boundary 作為 stable cursor evidence；presentation、
history、Read／Skip 與 Replay 亦使用相同 identity。只有完全沒有 `loc_id` 的 legacy
資料才保留 positional fallback，詳見
[stable-line-identity.md](stable-line-identity.md)。

`state.meta` 的私有 key（`__npc_registry__`、`__pack_story_catalog__`、plugin manager、
`__pending_toasts__` 等）在 save 時會被過濾。App 啟動／New Game 時從 pack YAML 建立
這些 runtime services；正式 load 不信任 save 裡的 catalog，而是在 detached
`GameState` 依序做 schema／pack migration、StoryGraph／map／ending／media rebind、
history/replay path 清理、choice-card refresh 與 resume provenance validation。所有
preflight 完成後才用 candidate 更新 live state 並保留目前 runtime bridges；任一錯誤都
讓 quickload／Continue／manual load 成為 state 與 scene-stack 的 atomic no-op。

`DialogueScene.restore_resume_context()` 對已存在的 scene 另有 UI-level transaction：
先捕捉 BGM／ambient／voice ownership，再在全新的 scene/widget dictionary 重建，成功後
才提交。Audio snapshot 建立失敗會在任何 mutation 前返回；renderer/backend 例外會換回
舊 widget，並盡力依 revision 與 exact voice checkpoint 還原 audio。這不把任意外部
plugin I/O 變成可 rollback transaction，plugin 作者仍需避免不可逆的 presentation side
effect。

成功 load 是明確 replacement boundary。SceneManager 用 hard cut 清掉 abandoned frame，
voice 一律停止；不同或 silent 的 BGM／ambient 以 `fade_ms=0` 停止後才嘗試目前 authored
track。`stop_music()`／`stop_ambient()` 會處理所有 outgoing slots，所以即使 tracked path
已是 `None`、mixer 還留著 fade tail 的 untracked channel 也會被切掉。替代素材缺失／
decoder 失敗時維持安靜；相同 tracked path 則不重啟。

Scene Replay 的 archive 也是資料、不是執行權威。Load 先剝除無法由目前 builtin line／
inbound transition 精確產生的 replay FX；播放前再驗證目前 StoryGraph／map／NPC 下的
有效 audio carry、line override、SFX／voice／CG／portrait、choice/card 與 speaker
identity。非 NPC 的歷史顯示名（例如插值後玩家姓名）可保留，但 archive 不能切換到另一
NPC backend。通過後的 trace 仍只注入 engine-owned presentation queue，不 dispatch
authored effect 或任意 plugin handler。

Plugin 呼叫由 `plugins.errors.isolate()` 捕捉一般 `Exception`、記錄
`PluginRuntimeError` 並讓其他 handler 繼續；system chrome、portrait／ambient backend
等 renderer 接縫也使用同一模式。這是 crash containment，不是安全 sandbox，也不取代
target-platform decoder、codec、記憶體與長跑驗證。
