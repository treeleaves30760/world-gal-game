# ROADMAP

World Gal-Game 引擎下階段發展路線。配合 [CLAUDE.md](CLAUDE.md)（AI 進場速查）
與 [docs/architecture.md](docs/architecture.md)（引擎內部結構）一起讀。

> 上次大更新：2026-05-19。**Phase 1 全數收斂、Phase 2 大半落地**；下一段焦點
> 已轉為「對外擴充體驗」與「AI 端到端自主開發 loop」。

---

## 0. TL;DR

引擎現況：

- 支柱 A（人類用 YAML 寫 Gal-Game） — **成熟**。
- 支柱 B（AI 用引擎做遊戲） — **基本完整**：除了 `HeadlessSession` / `GameDriver`，
  另有 `PackEditor`（結構化編輯）、`PackInspector`（pack 結構視角）、
  `CapabilityManifest`（機器可讀能力清單）、`SelfCheck`（五階段驗證）、
  `SmokeRunner` / `VisualCheck` / `asset_studio`。
- 支柱 C（第三方插件擴充） — **MVP 已上線**：Phase 1 四種擴充點
  （`@effect` / `@condition` / `@hook` / `@inspect_field`）全部落地，
  Phase 2 三種擴充點（`@widget` / `@scene` / `@brain` / `@dialogue_op`）的
  registry 與 decorator 也都存在，但 `PluginManifest.extends` schema 還沒同步
  公開這幾個欄位（manifest 落後 registry 一步）。

**下一段焦點**：補齊 Phase 2 的「manifest schema 對齊」 + 開始 Phase 3 的
「AI 端到端自主開發 loop」與「分發機制」。

---

## 1. 願景與三大支柱

### 1.1 引擎與遊戲包分離

引擎（`world_gal_game/`）只提供 runtime、core dataclasses、UI primitives、
headless driver、save 系統 — **零遊戲特定邏輯**。遊戲是「pack」，可放在
`games/<pack>/`、sibling repo、使用者目錄、或 `--pack <path>` 指定的任何位置。

這個分離是引擎所有設計決策的源頭。

### 1.2 支柱 A — 人類玩家用引擎做遊戲

YAML pack authoring：

- `meta.yaml`（含 `pack_format_version: "0.1"`） 描述 pack 中繼資料、起始狀態、
  theme / locale 覆寫
- `content/scenes/*.yaml` 寫對話、選項、effect、condition
- `content/{characters,locations,items,quests,achievements,clues}.yaml` 各系統定義
- `content/clues.yaml`（新增）— Clue / Journal 系統
- `tools/scaffold_pack.py` 生成新 pack skeleton
- `world_gal_game/dev/asset_studio.py`（新增）— placeholder PNG、resize、convert

### 1.3 支柱 B — AI 工具用引擎做遊戲

「AI 做所有原本開發者會做的事」的清單與現況：

| 開發行為 | 現況 |
|---|---|
| 讀懂 pack 結構 | **是**：`PackInspector` 提供 summary / scenes / locations / npcs / items / reachability / dead-ends / mermaid graph |
| 跑 pack 看結果 | **是**：`HeadlessSession` + `GameDriver` |
| 結構化編輯 pack | **是**：`PackEditor`（comment-preserving，ruamel.yaml round-trip，dry-run + diff） |
| 加新 effect / condition | **是**：`@effect` / `@condition` 直接擴充 registry |
| 加新 widget / scene / brain / dialogue_op | **部分**：registry + decorator 都有，但 `PluginManifest.extends` schema 還沒接這四個欄位 |
| 自我驗證（5 階段） | **是**：`SelfCheck` 串接 schema → refs → dead-ends → smoke → visual |
| 生成 placeholder 素材 | **是**：`asset_studio.placeholder_image` / `resize` / `convert` / `stock_placeholder_pack` |
| 看引擎能力 | **是**：`build_manifest()` / `wgg capabilities --pack <pack>` |
| LLM 驅動 NPC 對話 | deferred Phase 3 |
| 從 spec → 跑通 pack | deferred Phase 3 |

詳見 §2、§3。

### 1.4 支柱 C — 第三方插件擴充引擎

讓使用者（包含 AI）不必改 core 就能擴充引擎能力。Phase 1 四種、Phase 2 四種，
**現況**：

| Decorator | Registry | Manifest 欄位 | 狀態 |
|---|---|---|---|
| `@effect` | `EFFECT_REGISTRY` | `extends.effects` | 完成 |
| `@condition` | `CONDITION_REGISTRY` | `extends.conditions` | 完成 |
| `@hook` | `HOOK_REGISTRY` | `extends.hooks` | 完成 |
| `@inspect_field` | `INSPECT_FIELD_REGISTRY` | `extends.inspect_fields` | 完成 |
| `@widget` | `WIDGET_REGISTRY` | （未加） | registry 已開，manifest 落後 |
| `@scene` | `SCENE_REGISTRY` | （未加） | 同上 |
| `@brain` | `BRAIN_REGISTRY` | （未加） | 同上 |
| `@dialogue_op` | `DIALOGUE_OP_REGISTRY` | （未加） | 同上 |

`HookEvent` 從原計畫 ~13 個擴張到 16 個（含 `SCENE_REPLACE` / `DIALOGUE_CHOICE_MADE`
/ `APP_FRAME` 等）。

---

## 2. 現況盤點

### 2.1 支柱 A 現況

- pack 模型：`docs/pack-format.md`、`docs/getting-started.md`、
  `docs/tutorial-build-a-game.md`、`docs/cookbook.md`
- 子系統 docs：`scenes.md`、`characters.md`、`affection.md`、`items.md`、
  `shops.md`、`achievements.md`、`resources.md`、`quests.md`、
  `theme-and-locale.md`、`locations.md`、`distribution.md`
- effect / condition 參考：`docs/effects-reference.md`、
  `docs/conditions-reference.md`（內容由 registry 動態生成的能力可作為下階段
  目標）
- 工具：`tools/scaffold_pack.py`、`world_gal_game/dev/asset_studio.py`
- 測試：`tests/` 下 25+ 個 test 檔（367 個 test case，全綠）；demo_pack 內附
  `games/demo_pack/scripts/test_{lover,friend,alone}_route.json` 三條主線通關
  script，被 `SmokeRunner` 自動掃描

### 2.2 支柱 B 現況

- `world_gal_game/headless.py` — `HeadlessSession` 高階 op（純 Python，無 pygame
  display）。op 包含 `start_scene`、`next`、`choose`、`move`、`advance_time`、
  `set_flag`、`adjust_affection`、`inspect`、`run_script`。詳見 `docs/headless.md`。
- `world_gal_game/dev/driver.py` — `GameDriver` 低階 pygame events，含
  `snapshot`、`screenshot`、`find_widget`。詳見 `docs/ai-debug.md`。
- `world_gal_game/dev/pack_inspector.py` — pack 結構視角（summary / scenes /
  locations / npcs / items / reachability / dead-ends / graph）。
- `world_gal_game/dev/pack_editor.py` — 結構化 CRUD（add/remove scene、
  npc、location、item、choice、update_*）；comment-preserving；dry-run + diff；
  錯誤以 `PackEditError`（含 field / path / expected / got / hint）回報。
- `world_gal_game/dev/capability_manifest.py` — `build_manifest()` /
  `manifest_json()` / `summary_table()` + 一票 `all_*_kinds()` / `find_*` API。
- `world_gal_game/dev/self_check.py` — 5 階段 pipeline（schema、refs、dead_ends、
  smoke、visual），輸出 JSON。
- `world_gal_game/dev/smoke_runner.py` — 掃 `scripts/test_*.json` 全跑一遍。
- `world_gal_game/dev/visual_check.py` — md5 + pixel diff 比對 baseline PNG。
- `world_gal_game/dev/asset_studio.py` — placeholder PNG / resize / convert。
- CLI 子命令：`wgg validate`（aka `wgg check`）、`wgg inspect-pack` /
  `--capabilities`、`wgg edit`、`wgg capabilities`、`wgg smoke`、
  `wgg visual-check`、`wgg self-check`、`wgg debug`。
- `--dev-start` / `--dev-flags` / `--dev-affection` / `--dev-location` /
  `--dev-time` 仍用來定向截圖。

### 2.3 支柱 C 現況

- `world_gal_game/plugins/` — 完整套件：`registry.py`、`manager.py`、
  `manifest.py`、`context.py`、`errors.py`、`builtin_effects.py`、
  `builtin_conditions.py`。
- 公開 API：8 個 decorator（4 個 Phase 1 + 4 個 Phase 2）、8 組 registry singleton、
  8 組 Entry / Registry 型別、`PluginContext`、`PluginManager`、`PluginManifest`、
  `HookEvent`、`ManifestError` / `DuplicateKindError` / `PluginRuntimeError`…
- 16 個 `HookEvent`（`pack.before_load` / `pack.after_load` / `game.state_ready`
  / `effect.before_apply` / `effect.after_apply` / `save.before_serialize` /
  `save.after_load` / `scene.push` / `scene.pop` / `scene.replace` /
  `dialogue.before_line` / `dialogue.after_line` / `dialogue.choice_made` /
  `player.move` / `time.advance` / `app.frame`）。
- 三個掃描根：`world_gal_game/plugins_user/`（engine 內附）、
  `~/.world-gal-game/plugins/`（per-user）、`<pack>/plugins/`（pack-local）。
- demo plugin：`games/demo_pack/plugins/step_counter/`（effect + condition + hook
  + inspect_field 都示範）。
- 隔離：handler 例外 `isolate()` 收斂為 log + safe-default，不會把單一 plugin
  的 bug 升級成 engine crash。
- 序列化：插件私有 state 寫 `state.meta["__plugin:<id>__"]`，`SaveManager` 過濾
  `__` 前綴；公開 field 走 `@inspect_field`（或未來的 `@field`）。
- `core/story_graph.py` 的 `Effect.kind` / `Condition.kind` 已從 `Literal[...]`
  改成 `str + field_validator`。
- `core/game_state.py` 39 個 if-elif 已換成 registry dispatch（含 hook fire 包裝）。

---

## 3. 缺口分析

### 3.1 支柱 A 缺口

- **`docs/effects-reference.md` / `conditions-reference.md` 是手寫**：能力清單已
  經機器可讀（`CapabilityManifest`），但兩份 reference 文件仍由人工維護，會與
  registry 漂移。
- **authoring UX 仍偏 YAML**：人類沒有「視覺化編輯器」；對 LLM-輔助寫 pack 來說
  夠用，對純人類作者來說還是要硬寫。

### 3.2 支柱 B 缺口

- **AI 自主從 spec 產出可玩 pack**：目前 AI 可以「玩 + 看 + 編輯 + 驗證 + 擴充」，
  但「從 idea 到通關」這條端到端 loop 還沒有官方範例。
- **`wgg edit` 與 `CapabilityManifest` 的結合**：`wgg edit` 寫入時不會主動 hint
  「你的 kind 不存在；最相近的是 …」；下次 PR 可以把 manifest 餵進 PackEditor
  的 validation hint pipeline。
- **`PackEditor.add_clue`、`add_quest`、`add_achievement`**：目前只覆蓋 scene /
  choice / npc / location / item / portrait；其他 pack-level 集合還沒專屬 mutator。

### 3.3 支柱 C 缺口

- **Manifest schema 落後 registry**：`PluginManifest.Extends` 還沒接
  `widgets` / `scenes` / `brains` / `dialogue_ops` 四個欄位。實作上插件可以註冊
  這四種擴充，但 `plugin.yaml` 無法宣告它們，所以「side-effects 透明度」一條被
  打破。需要：
  - `Extends` model 加四個 list 欄位
  - `PluginManager.activate` 比對宣告與註冊
  - `docs/plugins.md` 加 widget / scene / brain / dialogue_op 範例段
- **插件依賴管理**：`depends` 欄位存在、`PluginManager` 也有拓樸排序，但
  循環依賴與版本範圍偵測仍是最小可行；下次有真的多 plugin 互相依賴的 pack 出來
  時要再加固。
- **跨 pack 共用插件**：插件分發機制（PyPI? `world-gal-game-plugins` 命名空間?
  pyproject 依賴? 簽章?）尚未設計。

---

## 4. 跨支柱共通設計挑戰

### 4.1 「插件即 AI 的擴充手段」

支柱 B（AI 擴充引擎）與支柱 C（第三方插件）走同一條路。目前實作維持這個原則：

- 註冊用 decorator（已實作）
- 擴充點有明確 schema（plugin.yaml + Python 雙保險，但 widget / scene / brain
  / dialogue_op 還沒把 schema 對齊回 manifest）
- 錯誤訊息結構化（`PackEditError` / `PluginRuntimeError` 已是 dataclass）

### 4.2 機器可讀的 Capability Manifest

`world_gal_game/dev/capability_manifest.py` 已完整實作：

- 內建 + 已載入插件的 effect / condition / hook / inspect_field 全集
- widget / scene / brain / dialogue_op 也納入（registry 側）
- 各 kind 的 plugin_id、description、signature
- `manager` 摘要：loaded / failed / disabled plugin

下次改進方向：把 `effects-reference.md` / `conditions-reference.md` 改成從
manifest 動態生成。

### 4.3 序列化邊界

`GameState._serialize_meta` field_serializer 過濾 `__` 前綴 key；
`SaveManager` 既有過濾邏輯保留。`@inspect_field` 提供讀路徑。寫公開 field 進
GameState 的 `@field` decorator 仍未實作（目前用 `state.meta["__plugin:<id>__"]`
存私有 state）。

### 4.4 信任邊界

- `plugin.yaml.side_effects.{reads_filesystem, writes_filesystem, network,
  subprocess, other}` 已存在（純宣告）
- `PluginManager.print_summary()` 載入後印一次摘要
- 每個 hook / effect / condition 呼叫包 `isolate()`（log + safe default）

---

## 5. 階段路線

### Phase 1 — 解鎖核心擴充能力 — **完成**

落實情況：

- 插件系統 MVP：✅ `world_gal_game/plugins/{registry,manager,manifest,
  context,errors,builtin_effects,builtin_conditions}.py`、demo 插件、`docs/plugins.md`、
  `tests/test_plugin_system.py`
- Capability Manifest：✅ `world_gal_game/dev/capability_manifest.py`
- PackEditor MVP：✅ `world_gal_game/dev/pack_editor.py`（含 dry-run + diff）
- PackInspector：✅ `world_gal_game/dev/pack_inspector.py`
- CLI：✅ `wgg edit` / `wgg check` / `wgg inspect-pack` / `wgg capabilities`
- AI 開發者指南：✅ `docs/ai-developer-guide.md`
- pack 驗證升級：✅ dead-end 偵測（`PackInspector.dead_ends`）已接進
  `SelfCheck`
- `pack_format_version`：✅ 已加進 demo_pack `meta.yaml`，`docs/pack-format.md`
  也更新

### Phase 2 — 擴大插件能力 + AI 端對端開發 loop — **大半完成**

落實情況：

- 擴充點：
  - ✅ `@widget` / `@scene` / `@brain` / `@dialogue_op` 在
    `registry.py` 已開
  - ❌ `PluginManifest.Extends` 的 schema 還沒對齊（下個 PR）
- ✅ Lifecycle hook：16 個 `HookEvent` 全部串好（超過原規劃 13 個）
- 🟡 插件依賴：`depends` + 拓樸排序已實作，循環依賴偵測最小可行
- ✅ AI 端對端 dev loop：
  - `world_gal_game/dev/self_check.py` — 五階段（schema → refs → dead-ends →
    smoke → visual）
  - `world_gal_game/dev/smoke_runner.py`
  - `world_gal_game/dev/visual_check.py`（md5 + pixel diff）
  - `world_gal_game/dev/asset_studio.py`
- 🟡 authoring CLI v2：`wgg edit` 還沒接 manifest hint；無 web preview

### Phase 3 — 分發 + LLM NPC + 自主性 — **未開工**

- pack 分發機制（PyPI namespace? archive 簽章? 版本相容矩陣?）
- LLM NPC v2 — 接 `ClaudeBrain`，`DialogueEngine` 支援動態驅動的對話 line
- AI 自主從 spec 產出可玩 pack（spec → pack → smoke → screenshot review → 迭代）
- 跨 pack 共用插件

---

## 6. 下個 PR 建議

按優先順序：

### 6.1（高槓桿、低工作量）對齊 manifest schema 與 Phase 2 registry

問題：`@widget` / `@scene` / `@brain` / `@dialogue_op` 已可用，但
`plugin.yaml` 無法宣告 → side-effect 透明度被打破、`wgg capabilities` 無法把
「插件聲稱要加什麼」與「插件實際加了什麼」對比。

範圍：

- `world_gal_game/plugins/manifest.py` — `Extends` 加 `widgets` / `scenes` /
  `brains` / `dialogue_ops` 四個 list（沿用 `ExtensionDeclaration` shape）
- `world_gal_game/plugins/manager.py` — `activate()` 比對宣告 vs 實際註冊，
  發現不符就 `warning`（不阻擋載入）
- `docs/plugins.md` — 補 `@widget` / `@scene` / `@brain` / `@dialogue_op`
  範例段；建議 `<pack>/plugins/widget_example/` 一個小 demo
- `tests/test_plugin_system.py` 或新檔 — 覆蓋宣告 / 實作不一致時的警告路徑

收益：把 Phase 2 收尾，避免「實作上開了、文件上沒開」造成的長期混淆。

### 6.2（中工作量、高長期價值）`effects-reference.md` / `conditions-reference.md` 動態生成

問題：兩份文件靠人工維護，跟 registry 漂移；插件作者要自己 grep code 才知道
真正的 kind 清單。

範圍：

- `tools/gen_reference.py`（新檔）— 跑 `build_manifest()` → 生成 markdown
- `docs/effects-reference.md` / `docs/conditions-reference.md` — 改成「自動產出，
  勿手改；改 builtin_effects.py 後執行 …」
- `pyproject.toml` — 把 `python tools/gen_reference.py` 加進 `[tool.uv]` 或
  README 的 dev loop

收益：插件作者多了一個「我寫了一個新 effect 後跑 `tools/gen_reference.py` 就
能看到自己被列上去」的回饋路徑。

### 6.3（中工作量、高使用者價值）`wgg edit` 接 CapabilityManifest hint

問題：AI 用 `wgg edit add-scene` 寫一個含「拼錯 kind」的 effect 時，目前的
`PackEditError` 只說「未知 kind」；如果能多一句「最相近的是 `move_to`」，
AI 重試成本就低很多。

範圍：

- `world_gal_game/dev/pack_editor.py` — kind 驗證失敗時呼叫
  `difflib.get_close_matches` 對 `EFFECT_REGISTRY.list_kinds()` 找出建議
- `tests/test_pack_editor.py` — 加 hint 路徑測試

收益：AI 開發體驗質感往上一階。

### 6.4（中工作量、Phase 3 起手）pack 分發機制設計

問題：要讓多個遊戲 pack 共用一個插件，現在只能各自 copy。

不在這個 PR 範圍；先寫一份 `docs/distribution-plugins.md` 設計提案（PyPI
namespace vs git submodule vs `pyproject.toml`-pinned 各自的權衡），徵求 review。

---

## 7. 風險與權衡

| 風險 | 對策 |
|---|---|
| **核心精簡 vs 插件能力強** | 採「core 只放 game-genre-agnostic primitives」原則。Phase 1 已落實。新主題的 effect 一律走插件。 |
| **AI 介面 vs 人類介面** | 不做兩套。`PackEditor` / `CapabilityManifest` 是同一 schema 的薄包裝，CLI 是其上的另一層。Phase 1 已落實。 |
| **向後相容** | `pack_format_version` 已加進 `meta.yaml`（"0.1"）；未來 schema 改動要加 migration path。 |
| **插件信任** | 不做 sandbox（單機遊戲）。`plugin.yaml` 必須明文宣告擴充點與副作用；載入時 `PluginManager.print_summary()` 印摘要。Phase 2 廣度擴大後要重新審視「使用者預期擴充什麼 vs 實際擴充什麼」。 |
| **demo_pack 鎖死** | 仍保留純內建 + step_counter 範例插件不依賴 LLM。 |
| **效能** | hook fire 在熱路徑（`effect.before_apply` / `effect.after_apply`、`app.frame`、`dialogue.before_line`、`dialogue.after_line`）— 目前 demo_pack 跑 smoke 仍在毫秒級，但 Phase 3 真的接 LLM brain 時要再 profile。 |
| **MCP server** | 仍不做。Claude Code / Codex 直接 import HeadlessSession / PackEditor / PackInspector / CapabilityManifest 即可。 |

---

## 8. 文件分工

| 檔案 | 角色 | 對象讀者 | 狀態 |
|---|---|---|---|
| `CLAUDE.md` | AI 進場速查 | Claude Code / Codex 進場時 | 已更新 Phase 1+2 完成版 |
| `ROADMAP.md` | 策略路線 | 維護者、貢獻者 | 本檔，2026-05-19 大更新 |
| `README.md` | 整體說明 | 第一次來的人 | 既有 |
| `docs/README.md` | docs 索引 | 任何人 | 既有 |
| `docs/architecture.md` | 引擎內部結構 | 想擴充核心的人 | 既有，已含「兩條路徑」段 |
| `docs/headless.md` | headless 操作 | AI / CI | 既有 |
| `docs/ai-debug.md` | UI debug 操作 | AI | 既有 |
| `docs/pack-format.md` | pack 格式 | pack 作者 | 既有，含 `pack_format_version` |
| `docs/effects-reference.md` | effect 全清單 | pack 作者 | 既有，手工維護（§6.2 改為動態生成） |
| `docs/conditions-reference.md` | condition 全清單 | pack 作者 | 既有，同上 |
| `docs/plugins.md` | 插件實作參考 | 插件作者、AI | 已建立；下次 PR 補 Phase 2 範例 |
| `docs/ai-developer-guide.md` | AI 完整開發指南 | AI | 已建立 |
| `docs/distribution.md` | pack 分發 | pack 作者 | 既有；plugin 分發 Phase 3 補 |

---

## 9. 三大支柱依賴關係（現況版）

```
                  +------------------------+
                  |  支柱 C: 插件系統 ✅      |
                  |  Plugin Registry +     |
                  |  Capability Manifest   |
                  +-----------+------------+
                              |
              providers       |        primitives
            registration      |       effect kinds,
                              v        condition kinds,
                  +------------------------+   widgets, scenes,
                  |  引擎核心 (core/) ✅     |    brains, dialogue_ops,
                  |  GameState.evaluate    |    16 hook events
                  |  GameState.apply       |
                  +------------------------+
                       ^               ^
        consumes (邏輯) |               | consumes (邏輯 + 編輯 + 驗證)
                       |               |
       +---------------+--+   +--------+-----------------+
       | 支柱 A: 人類做遊戲 |   | 支柱 B: AI 做遊戲 ✅       |
       | YAML + scaffold  |   | HeadlessSession +        |
       | + asset_studio   |   | GameDriver + PackEditor  |
       | + clue system    |   | + PackInspector +        |
       |                  |   | CapabilityManifest +     |
       |                  |   | SelfCheck/SmokeRunner/   |
       |                  |   | VisualCheck              |
       +---------+--------+   +-----------+--------------+
                 |                        |
                 +----- 共用 pack 格式 ----+
                       content/*.yaml
                  pack_format_version: 0.1
```

關鍵依賴（仍適用）：

- **B 依賴 C**：AI 擴充走插件 API。已實作。
- **C 依賴 B 的介面契約**：插件 API declarative-first。已實作。
- **A 也依賴 C**：人類新增 effect 也走插件，core 只放通用 primitives。已實作。
- **A 與 B 共用 pack 格式**：`pack_format_version` 控制 migration。佔位已加。

---

## 10. 變更紀錄 / 里程碑追蹤

| 日期 | 里程碑 | 狀態 | 備註 |
|---|---|---|---|
| 2026-05-18 | 藍圖確立（CLAUDE.md + ROADMAP.md） | 完成 | — |
| 2026-05-18 | Phase 1: 插件系統 MVP | 完成 | `plugins/{registry,manager,manifest,context,errors,builtin_*}.py`、`@effect`/`@condition`/`@hook`/`@inspect_field` |
| 2026-05-18 | Phase 1: Capability Manifest | 完成 | `dev/capability_manifest.py` + `wgg capabilities` |
| 2026-05-18 | Phase 1: PackEditor MVP | 完成 | `dev/pack_editor.py`（含 dry-run + diff） |
| 2026-05-18 | Phase 1: PackInspector | 完成 | `dev/pack_inspector.py` + `wgg inspect-pack` |
| 2026-05-18 | Phase 1: pack 驗證升級 | 完成 | dead-end 已進 `SelfCheck`；`pack_format_version` 加入 |
| 2026-05-19 | Phase 2: 擴大插件擴充點（registry 側） | 完成 | `@widget`/`@scene`/`@brain`/`@dialogue_op` |
| 2026-05-19 | Phase 2: 16 lifecycle hook | 完成 | 超過原計畫 13 個 |
| 2026-05-19 | Phase 2: AI 端對端開發 loop | 完成 | `SelfCheck` + `SmokeRunner` + `VisualCheck` + `asset_studio` |
| 2026-05-19 | Clue / Journal 系統 | 完成 | 額外功能（不在原 roadmap）；`core/clue.py`、`scenes/clues_scene.py`、`ui/widgets/clue_log.py`、`content/clues.yaml` |
| 待定 | Phase 2 收尾：manifest schema 對齊（§6.1） | 未開工 | 下個建議 PR |
| 待定 | Phase 2 收尾：references 動態生成（§6.2） | 未開工 | — |
| 待定 | Phase 2 收尾：`wgg edit` hint 升級（§6.3） | 未開工 | — |
| 待定 | Phase 3: pack 分發機制 | 未開工 | — |
| 待定 | Phase 3: LLM NPC v2 | 未開工 | — |
| 待定 | Phase 3: AI 自主 spec → pack | 未開工 | — |

Phase 收尾 PR 開工時於此表追蹤，並在每個 PR 描述中引用對應里程碑。
