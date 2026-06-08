# AI Developer Guide

> **學習路徑**：軌道 4a · AI 工具當開發者  
> **前置條件**：讀過 [CLAUDE.md](https://github.com/treeleaves30760/world-gal-game/blob/main/CLAUDE.md)（進場速查）+ [architecture.md](architecture.md)  
> **下一步**：想擴充引擎 → [plugins.md](plugins.md)；想做新 pack → [tutorial-build-a-game.md](tutorial-build-a-game.md)  
> **完整索引**：[docs/README.md](README.md)

---

寫給 AI 協作者（Claude Code、Codex）的端對端使用手冊。

引擎不只是「給 AI 看到遊戲」，而是讓 AI **像開發者一樣** 全程參與：讀、跑、看、
編輯、擴充、驗證、生成。這份文件把這些工具集中介紹一次。

---

## 1. 一句話定位

你是「能寫遊戲的人」，不是「能 input 按鍵的 NPC」。引擎為你準備了五組 API：

| 角色 | 工具 | 目的 |
|---|---|---|
| 玩家 | `HeadlessSession`、`GameDriver` | 跑遊戲、抓 state 與截圖 |
| 編輯者 | `PackEditor` | 結構化改 pack YAML（保留註解） |
| 偵測者 | `PackInspector` | 看 pack 拓樸、reachability、dead-end |
| 觀察者 | `CapabilityManifest` | 看引擎支援哪些 effect / condition / hook |
| 擴充者 | 插件 API | 加新 effect / condition / hook / inspect 欄位 |

下面分章節介紹每組工具。

---

## 2. 工具總覽

### Python API

```python
# 玩遊戲 / 抓玩家狀態
from world_gal_game.headless import HeadlessSession
from world_gal_game.dev.driver import GameDriver

# 看 pack 拓樸
from world_gal_game.dev.pack_inspector import PackInspector

# 改 pack
from world_gal_game.dev.pack_editor import PackEditor, PackEditError

# 看引擎能力
from world_gal_game.dev.capability_manifest import (
    build_manifest, manifest_json, find_effect, find_condition,
)

# 寫插件
from world_gal_game.plugins import (
    effect, condition, hook, inspect_field, HookEvent, PluginManifest,
)
```

### CLI（裝完 engine 後 `world-gal-game` 或 `wgg`）

```bash
wgg --pack <p>                     # 啟動 GUI
wgg --headless --inspect --pack <p> # 印玩家狀態 JSON
wgg --headless --script <json>     # 跑腳本（多步驟）
wgg debug <repro.json>             # 注入點擊 + 截圖
wgg check <pack>                   # 全套驗證（schema + refs + dead-ends）
wgg inspect-pack <pack> [--format] # PackInspector CLI
wgg edit <pack> <op> [--dry-run]   # PackEditor CLI
wgg capabilities [--pack <p>]      # 印引擎能力 manifest
```

---

## 3. 玩 / 看遊戲

詳見 [headless.md](headless.md) 與 [ai-debug.md](ai-debug.md)。簡要：

```python
from world_gal_game.headless import HeadlessSession
from world_gal_game.config import EngineConfig

sess = HeadlessSession.open(EngineConfig(), pack="demo_pack")
sess.start_scene("prologue")
sess.next_line(10)
sess.choose("accept_quest")
snap = sess.inspect()
print(snap["flags"], snap["location"])
```

`HeadlessSession.inspect()` 給的是「玩家視角」的狀態。要看 pack 結構（多少 scene、
reachability 等）用 PackInspector — 兩者互補。

`GameDriver` 是低階 pygame events + screenshot。給 UI bug repro 用。

---

## 4. 看 pack 結構

### PackInspector

```python
from world_gal_game.dev.pack_inspector import PackInspector

ins = PackInspector("games/demo_pack")
print(ins.summary())
# {
#   'title': '小鎮的午後',
#   'pack_format_version': '0.1',
#   'counts': {'scenes': 11, 'locations': 4, ..., 'endings': 3},
#   ...
# }
```

可用方法：

| 方法 | 回傳 | 用途 |
|---|---|---|
| `summary()` | dict | 高階 rollup |
| `scenes()` | list[dict] | 每個 scene 的 outgoing 邊 |
| `locations()` | list[dict] | exits + scene_hooks |
| `npcs()` / `items()` | list[dict] | NPC / item 基本資料 |
| `reachability(start=None)` | dict | 從 intro_scene + scene_hooks BFS 走到哪 |
| `dead_ends()` | list[DeadEnd] | 走不下去的 scene / location / orphan |
| `graph(format="mermaid"\|"dot"\|"dict")` | str/dict | 視覺化 scene 圖 |

### CLI

```bash
wgg inspect-pack games/demo_pack
# pack: 小鎮的午後  (format v0.1)
#   counts:      scenes=11, locations=4, characters=2, ..., endings=3
#   reachable:   11 / 11 scenes
#   endings:     reachable=[...] unreachable=[]
#   dead-ends:   none

wgg inspect-pack games/demo_pack --format mermaid > graph.mmd
wgg inspect-pack games/demo_pack --format json | jq '.dead_ends'
```

PackInspector 操作 raw YAML（不會跑 plugin、不會載 GameState），純函式且快。

---

## 5. 看引擎能力（Capability Manifest）

知道「現在 effect 有哪些 kind 可以用」是寫 pack / 寫 plugin 的前提。

```python
from world_gal_game.dev.capability_manifest import (
    build_manifest, find_effect, all_effect_kinds,
)

print(all_effect_kinds())
# ['advance_time', 'affection', ..., 'use_item']

print(find_effect("affection"))
# {
#   'kind': 'affection',
#   'plugin_id': 'builtin',
#   'description': "Adjust a character's affection...",
#   'signature': {'target': 'character_id', 'value': 'int (delta)', ...}
# }

m = build_manifest()
# Full machine-readable snapshot of effects/conditions/hooks/plugins.
```

CLI：

```bash
wgg capabilities                     # 純引擎能力（builtin）
wgg capabilities --pack demo_pack    # 包含該 pack 載入的 plugins
wgg capabilities --pack demo_pack --format json > caps.json
```

**用法建議（給 AI 自己）**：在生成 pack YAML 之前先讀一次 manifest，知道
`signature` 提示什麼欄位 → 寫對的 effect/condition payload。

---

## 6. 編輯 pack（PackEditor）

PackEditor 是 ruamel.yaml round-trip 的薄包裝。它做兩件事：

1. **保留註解 / 引號 / 空行 / key 順序** — 不破壞 pack 作者的格式
2. **跑 pydantic 驗證** — 失敗用 `PackEditError(field, hint, ...)` 報告，AI 可以
   reactive 修 payload 後重試

### 最小範例

```python
from world_gal_game.dev.pack_editor import PackEditor

editor = PackEditor("games/demo_pack")
editor.add_scene({
    "id": "side_chat",
    "title": "閒聊",
    "lines": [{"speaker": "heroine_1", "text": "嗨。"}],
    "choices": [
        {"id": "ch_smile", "text": "微笑回應",
         "effects": [{"kind": "affection", "target": "heroine_1", "value": 5}]},
    ],
})
editor.add_choice("meet_heroine", {
    "id": "ch_extra", "text": "問個問題", "next_scene": "side_chat",
})
```

寫法等效於改 YAML，但會：
- 預設加到 `content/scenes/_generated.yaml`（或 `--into-file`）
- 跑 `Scene` / `Choice` pydantic 驗證
- 與既有 scene 的 id 衝突會立刻拋 `PackEditError`

### Dry-run + diff

```python
editor = PackEditor("games/demo_pack", dry_run=True)
editor.add_npc({"id": "rival", "name": "競爭者", "role": "antagonist"})
print(editor.diff())   # unified diff between disk & pending
editor.commit()        # 確定要寫，呼叫 commit
# 或 editor.rollback()  # 全部丟掉
```

### 全部 mutator

| 操作 | 目標檔案（預設） |
|---|---|
| `add_scene(scene, into_file=...)` | `scenes/_generated.yaml` |
| `update_scene(id, updates)` | scene 所在檔 |
| `remove_scene(id)` | scene 所在檔 |
| `add_choice(scene_id, choice)` | scene 所在檔 |
| `add_npc(npc, into_file=...)` | `characters.yaml` |
| `update_npc(id, updates)` / `remove_npc(id)` | `characters.yaml` |
| `add_location(loc, into_file=...)` | `locations.yaml` |
| `update_location(id, updates)` / `remove_location(id)` | `locations.yaml` |
| `add_item(item, into_file=...)` | `items.yaml` |

### CLI

```bash
wgg edit games/demo_pack add-npc \
    --payload '{"id":"rival","name":"競爭者","role":"antagonist"}' --dry-run

wgg edit games/demo_pack add-scene \
    --payload-file new_scene.json --into-file scenes/extra.yaml
```

### 錯誤處理（給 AI）

`PackEditError` 是 dataclass + Exception。重要欄位：

```python
try:
    editor.add_scene({"id": "x", "title": "X", "unknown_field": "oops",
                      "lines": []})
except PackEditError as e:
    print(e.to_dict())
    # {
    #   'op': 'add_scene', 'field': 'unknown_field', 'got': "'oops'",
    #   'hint': "'unknown_field' is not a known field on Scene; valid fields: [...]",
    #   ...
    # }
```

AI 看到 hint 後應該修 payload 重試，而不是放棄。

---

## 7. 擴充引擎（寫插件）

完整文件：[plugins.md](plugins.md)。

當你需要的功能 builtin kind 不支援（例如「跑酷小遊戲分數」），首選**寫插件**，
不要直接改 `core/`。

### 最小插件（一個 effect kind）

```
games/<pack>/plugins/parkour_score/
├── plugin.yaml
└── plugin.py
```

```yaml
# plugin.yaml
id: parkour_score
name: Parkour Score
version: 0.1.0
engine_version: ">=0.1.0"
extends:
  effects:
    - kind: add_parkour_score
      description: Bump the parkour mini-game score
      signature: {target: "<unused>", value: "int"}
```

```python
# plugin.py
from world_gal_game.plugins import effect

@effect("add_parkour_score",
        description="Bump the parkour score by value.",
        signature={"value": "int (delta)"})
def add(state, eff):
    slot = state.meta.setdefault("__plugin:parkour_score__", {"score": 0})
    slot["score"] += int(eff.value or 1)
    return {"kind": eff.kind, "score": slot["score"]}
```

Pack YAML 立刻可以用：

```yaml
effects:
  - {kind: add_parkour_score, value: 10}
```

### 完整擴充點

- `@effect(kind)` — 新 effect 種類
- `@condition(kind)` — 新 condition 種類
- `@hook(event)` — 訂閱 lifecycle 事件（`pack.after_load`、`effect.after_apply`…）
- `@inspect_field(key)` — 加 inspect 欄位

---

## 8. 端對端開發 loop

AI 從 spec 產出可玩 pack 的標準流程：

```
1. 讀 spec / user request
        │
        ▼
2. wgg capabilities --pack <existing_pack>     # 知道能用什麼 effect / condition
        │
        ▼
3. PackEditor.add_scene / add_choice / ...     # 構建 pack
        │   (失敗時讀 PackEditError.hint 修 payload 重試)
        ▼
4. wgg check <pack>                            # schema + refs + dead-ends 一次驗
        │   (有 error 修，warning 視情況)
        ▼
5. wgg inspect-pack <pack>                     # 看拓樸對不對
        │   (reachability 看 ending 走得到嗎)
        ▼
6. wgg --headless --pack <pack> --script test.json   # smoke 跑一輪
        │   (ending_* flag 對嗎)
        ▼
7. wgg --pack <pack> --screenshot out.png      # 截圖檢查 UI
        │
        ▼
8. iterate
```

每一步都可寫成單一 Python script 自動化。

### Smoke test 範本

```json
{
  "commands": [
    {"op": "start_scene", "scene": "prologue"},
    {"op": "next", "count": 20},
    {"op": "choose", "choice": "accept"},
    {"op": "move", "location": "town_square"},
    {"op": "next", "count": 30},
    {"op": "inspect"}
  ]
}
```

跑：

```bash
wgg --headless --pack <pack> --script smoke.json | jq '.commands[-1].result.flags'
```

---

## 9. 常見陷阱

- **直接 `open(yaml_path).write()` 改 pack 會洗掉註解** — 用 PackEditor。
- **以為 `Effect.kind` 還是 `Literal[...]`** — Phase 1 起改成開放 str；不在 registry
  的 kind 會在 `state.apply()` 回 `{"error": "unknown effect"}`，不會 raise。
- **以為 `HeadlessSession.move_to` 會觸發 `effect.after_apply` hook** — 不會；
  那是 dev shortcut。用 `state.apply(Effect(kind="move_to", target=...))` 才會
  fire hook。
- **以為 PackInspector 等同 `state.inspect()`** — 不等。前者是 pack 拓樸（檔案
  分析），後者是玩家狀態。
- **改 `core/game_state.py` 加新 effect kind** — 不要。新 kind 走 plugin。
- **沒讀 manifest 就寫 pack YAML** — 你不會知道 plugin 加了什麼 kind。先 `wgg
  capabilities --pack <p>` 一次。

---

## 10. 開發節奏建議

寫一支新 pack 時，建議的 commit 順序：

1. **骨架**：`tools/scaffold_pack.py --name <slug> --title "..."` 產出 skeleton
2. **能力盤點**：`wgg capabilities` 看 effect / condition 都有什麼
3. **核心 scene**：寫 intro_scene + 一條完整 route 通到一個 ending
4. **跑 smoke**：`wgg --headless --script .../smoke.json` 確認跑得通
5. **加分支**：另外的 ending、side quest
6. **驗證**：`wgg check`、`wgg inspect-pack`
7. **加插件**：有要加的特殊機制就寫 plugin
8. **截圖 review**：`wgg --screenshot out.png`

每一步都很短，每一步都可以 verify。AI 在這個 loop 裡的角色：把每個步驟自動化，
讓人類只看 review。

---

## 路線

完整 Phase 路線見 [ROADMAP.md](https://github.com/treeleaves30760/world-gal-game/blob/main/ROADMAP.md)。Phase 2 會加：
- `world_gal_game/dev/self_check.py`：把 schema / refs / dead-end / smoke / visual
  五階段整合
- `world_gal_game/dev/visual_check.py`：截圖 baseline 比對
- `world_gal_game/dev/asset_studio.py`：placeholder 圖、resize、轉檔
- 插件擴充點擴大到 widget / scene / brain
