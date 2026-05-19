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
  ├── 構造 SceneContext（彙整以上所有）
  └── 把 TitleScene 推上 SceneManager

App.run() 主迴圈：
  ├── 每幀：
  │     ├── pygame.event.get() → InputState.collect()
  │     ├── manager.update(dt, inp)
  │     ├── _poll_achievement_toasts() ← apply_all 推進的事件 / 物品 / 資源 deltas
  │     ├── toast_stack.update / draw
  │     └── pygame.display.flip()
  └── 偵測 inp.quit_requested 退出
```

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

整個 `GameState` 是 pydantic — `model_dump()` 拿到 dict，
`SaveManager.save()` 寫成 JSON。

`state.meta` 的私有 key（`__npc_registry__`、`__pending_toasts__`）
在 save 時會被 `SaveManager.save()` 過濾掉，避免把 NPCRegistry 物件序列化。

`SaveManager.load()` 重建 GameState 後，**還需要重新 bind 那些
class-level / module-level localization 與 npc registry** — 目前 App
重新走 `load_pack` 來做這事。意思是：載入存檔 = 重新讀 YAML + 然後把
玩家狀態套上去。
