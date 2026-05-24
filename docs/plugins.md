# Plugins

> **學習路徑**：軌道 4b · 寫插件擴充引擎本體  
> **前置條件**：讀完 [architecture.md](architecture.md)（懂 GameState 與 effect 分派）+ [pack-format.md](pack-format.md)  
> **下一步**：實際寫一支插件，跑 `wgg capabilities --pack <pack>` 看自己的擴充點被列出來  
> **完整索引**：[docs/README.md](README.md)

---

插件是擴充引擎能力的官方路徑。不必動 `world_gal_game/core/` 一行程式碼，
就可以新增 effect / condition / hook / scene / widget / brain / dialogue_op /
portrait_backend。第三方主題（恐怖、戀愛、養成…）、自訂遊戲機制、AI 工具產生的
新功能 — 都應該走插件。

九種擴充點（Phase 1 開了 4 個，Phase 2 補齊 4 個，Phase 5A 加上 portrait backend）：

| Decorator | 用途 | Phase |
|---|---|---|
| `@effect(kind)` | 新 effect 種類 | 1 |
| `@condition(kind)` | 新 condition 種類 | 1 |
| `@hook(event)` | 訂閱 lifecycle 事件 | 1（已 7 個事件，Phase 2 擴到 16 個） |
| `@inspect_field(key)` | 加 inspect 欄位 | 1 |
| `@widget(name)` | 新 pygame widget | 2 |
| `@scene(scene_id)` | 新 Scene 類別 | 2 |
| `@brain(name)` | 新 LLM Brain 實作 | 2 |
| `@dialogue_op(name)` | 新 `[[name:arg]]` 內嵌指令 | 2 |
| `@portrait_backend(name)` | 立繪渲染後端（動態立繪） | 5A |

---

## 一句話定位

一個 plugin 是一個目錄，含一份 `plugin.yaml` manifest 加一個 Python entry
module（預設名為 `plugin.py`）。entry module 用 decorator 註冊 handler 到全域
registry；引擎在 pack 載入時掃描 + 載入。

---

## 插件可以放哪裡

由 `PluginManager` 依序掃描三個位置：

| 位置 | 用途 | 範例路徑 |
|---|---|---|
| `world_gal_game/plugins_user/` | 引擎內附（隨 engine 發佈） | `world_gal_game/plugins_user/my_plugin/` |
| `~/.world-gal-game/plugins/` | 使用者全域 | `~/.world-gal-game/plugins/my_plugin/` |
| `<pack_root>/plugins/` | pack 內附（只對該 pack 生效） | `games/demo_pack/plugins/step_counter/` |

同 ID 衝突時：pack > user > engine（後者被覆蓋）。

---

## 完整範例：step_counter

跟著 `games/demo_pack/plugins/step_counter/` 對讀。這支插件展示 Phase 1 全部
四種擴充點。

### 目錄結構

```
games/demo_pack/plugins/step_counter/
├── plugin.yaml      # manifest
└── plugin.py        # entry module
```

### `plugin.yaml`

```yaml
id: step_counter
name: Step Counter
version: 0.1.0
description: |
  Tracks how many times the player has moved between locations.
author: World Gal-Game examples
engine_version: ">=0.1.0"
depends: []
entry_module: plugin

extends:
  effects:
    - kind: reset_step_counter
      description: Reset the step counter to zero.
      signature:
        target: "<unused>"
        value: "<unused>"
  conditions:
    - kind: steps_gte
      description: True when the player has walked at least `value` steps.
      signature:
        value: "int (minimum step count)"
  hooks:
    - kind: effect.after_apply
      description: Increment the counter on every successful move_to.
  inspect_fields:
    - kind: step_counter
      description: "Surfaces {count: int} into the headless inspect snapshot."

side_effects:
  reads_filesystem: false
  writes_filesystem: false
  network: false
  subprocess: false

tags:
  - example
  - tutorial
```

每個 `extends.*` 條目都是宣告 — 它告訴 Capability Manifest 與未來的 PackEditor
「這個插件提供了什麼」。實際註冊還是要 Python 端做。

### `plugin.py`

```python
from world_gal_game.plugins import (
    effect, condition, hook, inspect_field, HookEvent,
)


def _slot(state):
    """Return (creating if missing) this plugin's private state dict."""
    key = "__plugin:step_counter__"
    slot = state.meta.get(key)
    if not isinstance(slot, dict):
        slot = {"count": 0}
        state.meta[key] = slot
    return slot


@effect("reset_step_counter",
        description="Reset the step counter to zero.")
def handle_reset(state, eff):
    slot = _slot(state)
    old = slot.get("count", 0)
    slot["count"] = 0
    return {"kind": eff.kind, "ok": True, "old": old, "new": 0}


@condition("steps_gte",
           description="True when steps taken >= value.",
           signature={"value": "int (minimum)"})
def cond_steps_gte(state, cond):
    slot = _slot(state)
    return slot.get("count", 0) >= int(cond.value or 0)


@hook(HookEvent.EFFECT_AFTER_APPLY,
      description="Count successful move_to dispatches.")
def on_effect_applied(ctx, eff=None, result=None):
    if eff is None or eff.kind != "move_to":
        return
    if isinstance(result, dict) and "error" in result:
        return  # the move failed — don't credit a step
    if ctx.state is None:
        return
    slot = _slot(ctx.state)
    slot["count"] = slot.get("count", 0) + 1


@inspect_field("step_counter",
               description="Number of successful location moves so far.")
def inspect_step_count(state):
    slot = _slot(state)
    return {"count": slot.get("count", 0)}
```

### 在 pack YAML 中使用

```yaml
# games/<pack>/content/scenes/example.yaml
- id: too_lazy_to_walk
  title: 走累了
  lines:
    - text: "你已經走了不少路了。"
  choices:
    - id: see_count
      text: "我已經走了 5 步以上嗎？"
      requires:
        - {kind: steps_gte, value: 5}
      effects:
        - {kind: log_event, target: "達成 5 步", value: ""}
        - {kind: reset_step_counter}
```

---

## Decorator API

### `@effect(kind, *, description="", signature=None, plugin_id=None)`

註冊一個 effect handler，handler 簽名是 `(state: GameState, eff: Effect) -> dict`。
回傳值會被加入 `apply_all` 的結果清單。

`signature` 是 free-form dict，供 Capability Manifest 與 PackEditor 拿來提示
「這個 kind 預期 target/value/stat 怎麼用」。引擎本身不強制驗證 signature。

`plugin_id` 通常不用傳 — 在 PluginManager 載入插件期間，decorator 會自動拿
context var 抓到正在載入的 plugin id。只有當你寫測試或在 REPL 直接呼叫
decorator 時才需要明寫。

### `@condition(kind, *, description="", signature=None, plugin_id=None)`

註冊 condition handler，簽名 `(state, cond) -> bool`。回傳 False 等同「條件
不滿足」。

### `@hook(event, *, description="", priority=100, plugin_id=None)`

訂閱引擎事件。Handler 簽名 `(ctx: PluginContext, **payload) -> None`。

事件清單見下方「Hook events」。`priority` 越小越早跑（預設 100）。多個插件
訂閱同一事件時，按 priority 升冪排序，priority 相同時按註冊順序。

### `@inspect_field(key, *, description="", plugin_id=None)`

新增一個 inspect 欄位，handler 簽名 `(state) -> Any`。值會被 `INSPECT_FIELD_REGISTRY.collect()`
收集起來，供 `HeadlessSession.inspect()` / PackInspector 加入輸出。

### `@widget(name, *, description="", signature=None, plugin_id=None)`  *(Phase 2)*

Class decorator：註冊一個 pygame widget class，給場景使用。Scene 作者可以
透過 `WIDGET_REGISTRY.spawn(name, *args)` 拿到實例。

```python
from world_gal_game.plugins import widget

@widget("score_badge",
        description="HUD that shows the player's score.")
class ScoreBadge:
    def __init__(self, rect, *, fonts, theme):
        self.rect = rect
        ...
    def draw(self, surface): ...
```

### `@scene(scene_id, *, description="", overlay=False, plugin_id=None)`  *(Phase 2)*

Class decorator：註冊自訂 `Scene` 子類別。透過 `SCENE_REGISTRY.spawn(scene_id, ctx)`
取得實例後 push 到 SceneManager。用來做 minigame、自訂 inventory、NPC 專用 UI 等。

```python
from world_gal_game.plugins import scene
from world_gal_game.scenes.base import Scene

@scene("parkour_minigame", overlay=False,
       description="A parkour-themed minigame.")
class ParkourScene(Scene):
    def update(self, dt, inp): ...
    def draw(self, surface): ...
```

### `@brain(name, *, description="", plugin_id=None)`  *(Phase 2)*

Class decorator：註冊自訂 `LLMBrain` 實作。引擎啟動時讀 `meta.yaml.brain`，
找對應 plugin brain；找不到就用 EchoBrain。

```python
from world_gal_game.plugins import brain
from world_gal_game.npc.llm_brain import LLMBrain

@brain("claude", description="Anthropic Claude-backed brain.")
class ClaudeBrain(LLMBrain):
    def respond(self, *, npc, system_prompt, user_context, history=None):
        ...
```

```yaml
# meta.yaml
brain: claude
```

### `@dialogue_op(name, *, description="", plugin_id=None)`  *(Phase 2)*

Function decorator：註冊 `[[name:arg]]` 內嵌指令。DialogueEngine 在渲染 line
前掃描文本，找到 `[[name:arg]]` 就呼叫 handler，token 從文本移除。Handler
回傳字串會替換 token；回傳 `None` 則 token 變空字串。

```python
from world_gal_game.plugins import dialogue_op

@dialogue_op("upper", description="UPPERCASE the argument.")
def upper(state, arg):
    return arg.upper()
```

```yaml
# 場景 YAML
- text: "她突然喊：[[upper:救命]]！"
# 渲染為：「她突然喊：救命！」
```

未知 op 會保留原樣（`[[unknown:foo]]`），讓作者一眼發現拼錯。

### `@portrait_backend(name, *, description="", plugin_id=None)`  *(Phase 5A)*

Class decorator：註冊一個「立繪渲染後端」。它是「**畫哪張立繪**」（`PortraitSpec`
解析）與「**它怎麼動**」（每幀繪製）之間的接縫 —— 讓核心不綁定任何特定動畫函式庫。
`PortraitSpec.backend`（預設 `"static"` = 不動，與過去一致）指名某個後端，該立繪
**安定後的待機動畫**就交給它；enter/exit/crossfade 轉場仍走既有 surface 路徑。
未註冊的後端名會優雅退回靜態繪製，所以缺插件不會弄壞畫面。

後端 class 由對話場景**每槽**實例化為 `cls(spec, assets, fallback_size)`，需實作
三個方法（見 `world_gal_game/ui/portrait_backend.py`）：

```python
from world_gal_game.plugins import portrait_backend
from world_gal_game.ui.portrait_backend import blit_fitted   # 與靜態路徑同幾何

@portrait_backend("wiggle", description="左右輕晃。")
class WiggleBackend:
    def __init__(self, spec, assets, fallback_size):
        self._surf = assets.resolve_portrait(spec, fallback_size=fallback_size)
        self._t = 0.0
    def update(self, dt):                  # 推進動畫時鐘
        self._t += dt
    def base_surface(self):                # 給轉場用的「靜止幀」（可回 None）
        return self._surf
    def draw(self, surface, rect, *, flip=False, alpha=255):
        import math
        dx = int(8 * math.sin(self._t * 3))
        blit_fitted(surface, self._surf,
                    rect.move(dx, 0), flip=flip, alpha=alpha)
```

```yaml
# 場景 YAML — 對白 line 上的 portraits（PortraitSpec list）
- text: "嗨！"
  portraits:
    - character: heroine_1
      backend: wiggle            # 指名後端
      backend_args: {amp: 8}     # 後端自取參數（核心不解讀）
```

引擎內建一個 **`animated_portraits`** 插件（`world_gal_game/plugins_user/`），
提供兩個 web-safe（純 pygame）後端：

- **`breath`** — 單張立繪的程序化待機（呼吸縮放 + 微幅起伏 + 可選晃動），**不需額外
  美術**。`backend_args`：`period` / `scale` / `bob` / `sway`。
- **`sprite`** — sprite-sheet 影格動畫。`backend_args`：`cols` / `rows` / `fps` /
  `frames`。

原生骨架（Live2D / Spine）刻意**不進核心**：它們需要平台專屬 SDK、`pygame` 下無可用
綁定，應以**桌面限定插件**提供 —— 上面的擴充點讓這成為可能。

---

## Hook events

| 事件常數 | 字串值 | 何時 fire | Payload | Phase |
|---|---|---|---|---|
| `HookEvent.PACK_BEFORE_LOAD` | `pack.before_load` | content_loader 開始讀 pack 之前 | `pack_root` | 1 |
| `HookEvent.PACK_AFTER_LOAD` | `pack.after_load` | YAML 全部讀完、GameState 構建好 | `pack_root`, `meta` | 1 |
| `HookEvent.GAME_STATE_READY` | `game.state_ready` | 啟動 clue sweep 後 | — | 1 |
| `HookEvent.EFFECT_BEFORE_APPLY` | `effect.before_apply` | `GameState.apply` 找 handler 之前 | `eff` | 1 |
| `HookEvent.EFFECT_AFTER_APPLY` | `effect.after_apply` | handler 跑完之後 | `eff`, `result` | 1 |
| `HookEvent.SAVE_BEFORE_SERIALIZE` | `save.before_serialize` | SaveManager 即將 model_dump | `slot`, `payload` | 1 |
| `HookEvent.SAVE_AFTER_LOAD` | `save.after_load` | SaveManager 完成 load | `slot`, `payload` | 1 |
| `HookEvent.SCENE_PUSH` | `scene.push` | SceneManager 推 scene 後 | `scene`, `kwargs` | 2 |
| `HookEvent.SCENE_POP` | `scene.pop` | SceneManager 拿掉 top scene 後 | `scene` | 2 |
| `HookEvent.SCENE_REPLACE` | `scene.replace` | SceneManager 替換底層 scene 後 | `old`, `new` | 2 |
| `HookEvent.DIALOGUE_BEFORE_LINE` | `dialogue.before_line` | DialogueEngine 即將呈現一句 | `scene_id`, `line_index`, `line` | 2 |
| `HookEvent.DIALOGUE_AFTER_LINE` | `dialogue.after_line` | DialogueEngine 呈現完一句 | `scene_id`, `line_index`, `line` | 2 |
| `HookEvent.DIALOGUE_CHOICE_MADE` | `dialogue.choice_made` | 玩家選了一個 choice | `scene_id`, `choice_id` | 2 |
| `HookEvent.PLAYER_MOVE` | `player.move` | `move_to` effect 成功後 | `from_location`, `to_location` | 2 |
| `HookEvent.TIME_ADVANCE` | `time.advance` | `advance_time` effect 後 | `phases`, `day`, `time_of_day` | 2 |
| `HookEvent.APP_FRAME` | `app.frame` | App 主迴圈每幀（保留，目前未自動 fire） | `dt` | 2 |

所有 hook 只在 `state.meta["__plugin_manager__"]` 存在時 fire — 也就是「透過
content_loader 載入的完整 pack 流程」。直接用裸 `GameState()` 不會 fire，這是
設計（讓單元測試不被 hook 干擾）。

---

## PluginContext

Hook handler 收到的第一個參數。常用屬性：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `state` | `GameState \| None` | 當前遊戲狀態（早期事件可能為 None） |
| `meta` | `dict` | meta.yaml 原始 dict |
| `pack_root` | `Path \| None` | pack 根目錄 |
| `manager` | `PluginManager \| None` | 載入這個插件的 manager |
| `config` | `EngineConfig \| None` | engine config |
| `scratch` | `dict` | hook 之間共享的暫存（請以 plugin id 加前綴） |
| `log` | `logging.Logger` | 寫 log 用 |

便利方法：

```python
slot = ctx.get_plugin_state("my_plugin")
# → state.meta["__plugin:my_plugin__"] 的 dict（不存在會自動建）

ctx.fire("my.custom_event", payload="hi")
# → 對 HOOK_REGISTRY 觸發任意事件，方便插件之間協同
```

---

## Private state 與存檔

插件需要持久化 state 時，**強烈建議**用以下 key 命名約定：

```python
state.meta[f"__plugin:{plugin_id}__"] = {...}
```

雙底線開頭的 key 會被 `SaveManager` 過濾掉，**不會**寫進存檔。如果你的 state
需要存檔，要嘛：

1. **可重算的**：用 `__plugin:...__` 前綴，存檔時自動清掉，由 hook
   `save.after_load` 重建（step_counter 是這個 pattern — 計數器本來就跟著
   `move_to` event 變動，存檔中重建即可）
2. **需要存的**：用無雙底線前綴的 key，例如 `state.meta["plugin_data:my_plugin"]`，
   `SaveManager` 會保留。注意 value 必須是 pydantic 可序列化的型別。

---

## 載入順序與依賴

`PluginManager.activate()` 用 Kahn's algorithm 對 `depends` 做拓樸排序：

```yaml
# plugin.yaml
depends:
  - some_other_plugin   # 必須先載入
```

- 找不到的依賴 → 該插件 `state="failed"`，error 是 `DependencyError`
- 循環依賴 → 環中所有插件 `state="failed"`，error 是 `DependencyError(cycle=[...])`
- 缺乏 `engine_version` 相容 → `state="failed"`，error 是 `IncompatibleEngineError`
- entry module import 失敗 → `state="failed"`，error 是 `PluginLoadError`

失敗的插件不會阻擋其他插件載入；它們在 `manager.failed()` 中可查到。

### engine_version 語法

| 寫法 | 意思 |
|---|---|
| `"*"` 或留空 | 任何版本 |
| `"0.1"` | prefix match — `0.1.x` 都算 |
| `">=0.1.0"` | 大於等於 |
| `"<=0.1.0"` | 小於等於 |
| `">=0.1.0,<2.0.0"` | 用逗號連 conjunction |
| `"~=0.1.5"` | PEP 440 compatible release：`>=0.1.5, <0.2` |

---

## side_effects 宣告

```yaml
side_effects:
  reads_filesystem: false
  writes_filesystem: false
  network: false
  subprocess: false
  other:
    - "啟動一個 daemon thread 監控 X"
```

引擎不強制檢查，但會在載入時 log 一行摘要 — 讓玩家 / 開發者一眼看到「啟用的
插件分別做了什麼」。Phase 2 之後可能會做動態 sandbox。

---

## Capability Manifest

執行時引擎已知的全部 kind / hook 清單可隨時查到：

```python
from world_gal_game.plugins import (
    EFFECT_REGISTRY, CONDITION_REGISTRY, HOOK_REGISTRY,
)

print(EFFECT_REGISTRY.list_kinds())
# → ['advance_time', 'affection', 'buy_item', ..., 'reset_step_counter']

print(EFFECT_REGISTRY.kinds_by_plugin())
# → {'builtin': ['advance_time', ...], 'step_counter': ['reset_step_counter']}

entry = EFFECT_REGISTRY.get("affection")
print(entry.signature)
# → {'target': 'character_id', 'value': 'int (delta)', ...}
```

PackEditor（Phase 1 後半）會自動讀這份 registry 來提示「你現在可以用的 kind」。

---

## 錯誤與隔離

設計原則：**單一插件失敗不能讓遊戲崩潰**。

- handler 內部例外 → log + 回傳 `{"kind": k, "error": "handler failed: ..."}`
- hook callback 內部例外 → log，主流程繼續
- duplicate kind → 在 decorator 註冊時就拋 `DuplicateKindError`（兩個插件
  搶同一個 kind 是設計錯誤，不該靜默吞掉）

要在測試裡讓例外重拋，用 `errors.isolate(..., reraise=True)` context manager。

---

## Do / Don't

**Do:**

- 用 `state.meta[f"__plugin:{id}__"]` 命名你的 private state
- 用 `signature=` 描述你 effect/condition 預期的 target/value/stat — 對 AI
  協作者非常重要
- 寫 `description` — Capability Manifest 與 docs 都會引用
- side_effects 老實宣告（reads_filesystem 等）
- 寫測試（看 `tests/test_plugin_system.py` 的 `_write_plugin` helper）

**Don't:**

- 不要直接 import `world_gal_game/core/...` 然後 monkey-patch — 用 hook 與
  registered handler，而不是改 core
- 不要在 module top-level 做 I/O（會在 import 時跑，會破壞 discovery）
- 不要為了「擋下別人 register」搶先註冊一個熱門 kind — 用 `depends:` 表達
  順序依賴
- 不要把 NPCRegistry / pygame surface / file handle 等存進 GameState — 它們
  序列化不來
- 不要假設你的插件是唯一的 — 多個插件可能同時 hook `effect.after_apply`，
  彼此不該互相干擾

---

## 進階：在 PluginContext.scratch 上協調多插件

兩個插件想互相通訊（不透過 GameState）：

```python
# plugin_a/plugin.py
from world_gal_game.plugins import hook, HookEvent

@hook(HookEvent.PACK_AFTER_LOAD)
def setup(ctx, **kw):
    ctx.scratch["plugin_a:loaded_at"] = ctx.state.time.day if ctx.state else 0

# plugin_b/plugin.py
@hook(HookEvent.GAME_STATE_READY, priority=200)
def react(ctx, **kw):
    a_day = ctx.scratch.get("plugin_a:loaded_at")
    if a_day is not None:
        ctx.log.info("plugin_b sees plugin_a loaded on day %d", a_day)
```

`scratch` 在 manager 生命週期內共享、不會序列化、不會跨 pack 重啟保留。

---

## 路線

Phase 1 + 2（本文件對應的範圍）：
- `@effect` / `@condition` / `@hook` / `@inspect_field`
- `@widget` / `@scene` / `@brain` / `@dialogue_op`
- 14 個 hook events 涵蓋 pack / save / scene / dialogue / player / time

Phase 3 預定加入：
- Plugin marketplace（跨 pack 共用插件）
- 動態 sandbox（限制不可信插件的副作用）
- 完整 LLM NPC v2（接 ClaudeBrain、DialogueEngine 動態驅動）
- AI 自主從 spec 產出可玩 pack

完整路線見 [ROADMAP.md](../ROADMAP.md)。
