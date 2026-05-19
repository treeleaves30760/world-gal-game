# CLAUDE — World Gal-Game Engine

給 AI 協作者（Claude Code、Codex 等）進場時讀的指引。完整策略路線見
[ROADMAP.md](ROADMAP.md)；引擎內部結構見 [docs/architecture.md](docs/architecture.md)。

---

## 一句話定位

**這是引擎，不是遊戲。** 遊戲叫「game pack」，獨立於引擎發佈，放在：

- `games/<pack>/`（隨引擎內附；demo_pack 就在這裡）
- `../<pack>/`（sibling repo）
- `~/.world-gal-game/packs/<pack>/`（使用者本地）
- 任何 `--pack <path>` 指定的位置

引擎 repo 是 `world_gal_game/`，提供 pygame runtime、core dataclasses（pydantic）、
場景框架、對話引擎、UI、headless driver、save 系統、插件系統。引擎本身**零遊戲特定邏輯** —
任何在 `games/` 下的變動都不是引擎變動。

---

## 三大支柱

引擎的目標不只是讓人類做遊戲，而是讓 AI 也能作為「開發者」全程參與：

- **A. 人類玩家用引擎做遊戲** — YAML pack 模型、scaffold tool、effect/condition reference
- **B. AI 工具用引擎做遊戲** — 「玩 + 看 + 編輯 + 擴充 + 驗證 + 生成」**已全部可用**
- **C. 第三方插件擴充引擎本體** — 不必改 core 就能加新 effect / condition / hook
  / inspect_field / widget / scene / brain / dialogue_op

Phase 1 + Phase 2 大半已落地，完整現況與路線見 [ROADMAP.md](ROADMAP.md)。

---

## 當前焦點

**Phase 2 收尾、Phase 3 起手。** 建議下一個 PR（按優先順序，詳見 ROADMAP §6）：

1. **對齊 manifest schema 與 Phase 2 registry**：`@widget` / `@scene` / `@brain`
   / `@dialogue_op` 已可在 Python 註冊，但 `plugin.yaml` 還不能宣告它們。把
   `PluginManifest.Extends` 加上四個 list，並在 `PluginManager.activate()` 做
   宣告 vs 實際註冊的一致性檢查。
2. **`docs/effects-reference.md` / `conditions-reference.md` 改成動態生成**：
   現在手寫易漂移。`tools/gen_reference.py` 用 `build_manifest()` 自動產出。
3. **`wgg edit` 接 CapabilityManifest hint**：寫入時把 kind 拼錯，回傳「最相近的是
   …」讓 AI 自動重試成本降低。

---

## 你（AI）能用的介面

### 玩 / 看遊戲

- `world_gal_game.headless.HeadlessSession` — 高階 op（`start_scene`、`next_line`、
  `choose`、`move_to`、`set_flag`、`adjust_affection`、`inspect`、`run_script`）。
  純 Python，不需 pygame display。見 `docs/headless.md`。
- `world_gal_game.dev.driver.GameDriver` — 低階 pygame events + screenshot + widget
  查詢。用於 pixel-level UI debug。見 `docs/ai-debug.md`。
- CLI：`wgg --headless --inspect`、`wgg --headless --script <json>`、
  `wgg --screenshot out.png`、`wgg debug <script.json>`。

### 看 pack 結構 / 看引擎能力

- `world_gal_game.dev.pack_inspector.PackInspector` — 開發者視角 inspect
  （`summary`、`scenes`、`locations`、`npcs`、`items`、`reachability`、
  `dead_ends`、`graph`（mermaid / dot））。
- `world_gal_game.dev.capability_manifest` — 機器可讀的引擎能力清單：
  `build_manifest()`、`manifest_json()`、`summary_table()`、
  `all_effect_kinds()` / `all_condition_kinds()` / `all_hook_events()` /
  `find_effect()` / `find_condition()`。
- CLI：`wgg inspect-pack <pack>`、`wgg inspect-pack <pack> --capabilities`、
  `wgg capabilities --pack <pack>`。

### 結構化編輯 pack

- `world_gal_game.dev.pack_editor.PackEditor` — 結構化 CRUD pack
  （`add_scene`、`add_npc`、`add_location`、`add_item`、`add_choice`、
  `update_*`、`remove_*`）。底層用 ruamel.yaml 保留註解。支援 `dry_run` +
  `diff()`。失敗以 `PackEditError`（含 `field` / `path` / `expected` / `got` /
  `hint`）回報。
- CLI：`wgg edit <pack> add-scene --payload '{"id":"…"}'` /
  `wgg edit <pack> add-choice --scene-id … --payload …` 等。

### 擴充引擎（寫插件而非改 core）

- 一個 plugin 是一個目錄（`plugins/<id>/`），含 `plugin.yaml` + Python entry
  module（預設 `plugin.py`）。entry 用 8 種 decorator 註冊 handler：
  - Phase 1：`@effect`、`@condition`、`@hook`、`@inspect_field`
  - Phase 2 registry 已開：`@widget`、`@scene`、`@brain`、`@dialogue_op`
    （但 `plugin.yaml.extends` 還沒同步開放這四個欄位 — 這正是下個 PR §6.1）
- 16 個 lifecycle `HookEvent`：`pack.before_load`、`pack.after_load`、
  `game.state_ready`、`effect.before_apply`、`effect.after_apply`、
  `save.before_serialize`、`save.after_load`、`scene.{push,pop,replace}`、
  `dialogue.{before_line,after_line,choice_made}`、`player.move`、
  `time.advance`、`app.frame`。
- 三個掃描根：`world_gal_game/plugins_user/`（engine 內附）、
  `~/.world-gal-game/plugins/`（per-user）、`<pack>/plugins/`（pack-local）。
- 範例：`games/demo_pack/plugins/step_counter/`（effect + condition + hook +
  inspect_field 都示範）。
- 詳見 `docs/plugins.md`、`docs/ai-developer-guide.md`。

### 自我驗證 / 端對端開發 loop

- `world_gal_game.dev.self_check.SelfCheck` — 五階段串接：schema → refs →
  dead_ends → smoke → visual，輸出 JSON-friendly `SelfCheckReport`。
- `world_gal_game.dev.smoke_runner.SmokeRunner` — 掃 `<pack>/scripts/test_*.json`
  全跑一遍；以「至少一個 `ending_*` flag 被設」做通過條件。
- `world_gal_game.dev.visual_check.VisualCheck` — `<pack>/visual_baselines/` 底下
  md5 + pixel diff 比對。
- `world_gal_game.dev.asset_studio` — `placeholder_image` / `resize` / `convert`
  / `stock_placeholder_pack`。
- CLI：`wgg self-check <pack>`、`wgg smoke <pack>`、`wgg visual-check <pack>`。

---

## 常用 dev loop（速查）

```bash
# 跑單元測試（367 個 case）
uv run pytest tests/

# 跑端到端通關（demo_pack 的 lover / friend / alone 三條主線）
uv run python main.py --headless --pack demo_pack \
    --script games/demo_pack/scripts/test_lover_route.json

# 五階段自我驗證（schema + refs + dead-ends + smoke；visual 預設 off）
uv run wgg self-check demo_pack

# 看 pack 當前狀態（玩家視角）
uv run python main.py --headless --inspect --pack demo_pack

# 看 pack 結構（開發者視角）
uv run wgg inspect-pack games/demo_pack
uv run wgg inspect-pack games/demo_pack --format mermaid

# 看引擎能力（含已載入插件）
uv run wgg capabilities --pack demo_pack

# 結構化編輯 pack（dry-run）
uv run wgg edit games/demo_pack add-scene \
    --payload '{"id":"sketch_demo","title":"…","lines":[…]}' --dry-run

# 截圖驗證 UI
uv run python main.py --pack demo_pack \
    --screenshot out.png --autoplay 1.0 --dev-start explore

# 啟動遊戲（dev）
uv run python main.py
```

---

## 重要原則

- **引擎核心不放任何遊戲特定邏輯**。新主題（恐怖 / 戀愛 / 養成）走插件，不直接改
  `world_gal_game/core/`。
- **加新 effect / condition 走 `@effect` / `@condition`**，不要改
  `world_gal_game/plugins/builtin_effects.py` 之外的 core 檔案。`core/game_state.py`
  的 `apply` / `evaluate` 已經是純 registry dispatch，沒有 39 個 if-elif 了。
- **不放裝飾性 emoji** — UI、文件、commit message、聊天輸出都不放。功能性符號（如
  箭頭、`→`、`✓` 在資料表格中）可接受。
- **pydantic v2 + YAML pack**。任何結構化資料先用 pydantic model 定義，再用 YAML
  序列化。
- **寫程式碼前先讀 [docs/architecture.md](docs/architecture.md)** — 內有模組分層、
  資料流、加新 effect / widget / scene 的範本。
- **引擎變動會影響所有 pack**。改 core schema 前先確認 demo_pack 還能跑通 smoke
  （`uv run wgg smoke demo_pack`）。`meta.yaml` 已加 `pack_format_version: "0.1"`，
  未來 schema 變動要對應 migration。
- **`GameState.apply` / `evaluate` 是中央分派**。任何狀態變更走它。直接寫
  state 欄位通常是 bug。
- **插件 handler 失敗不應該擊潰引擎**。所有 effect / condition / hook 呼叫都被
  `isolate()` 包起來，例外 log 後降級為 safe default。寫插件時不要依賴 caller 會
  catch — 自己 try/except 應該到位。

---

## 關鍵檔案速查

| 想做什麼 | 看哪裡 |
|---|---|
| 引擎內部結構 | `docs/architecture.md` |
| AI 完整開發指南 | `docs/ai-developer-guide.md` |
| AI 玩遊戲 | `docs/headless.md` |
| AI debug UI | `docs/ai-debug.md` |
| pack 格式 | `docs/pack-format.md` |
| effect 全清單 | `docs/effects-reference.md`（或 `wgg capabilities`） |
| condition 全清單 | `docs/conditions-reference.md`（或 `wgg capabilities`） |
| 寫第一個 pack | `docs/getting-started.md`、`docs/tutorial-build-a-game.md` |
| 寫插件 | `docs/plugins.md` |
| 常見模式 | `docs/cookbook.md` |
| GameState 中央分派 | `world_gal_game/core/game_state.py` |
| pack 載入入口 | `world_gal_game/content_loader.py` |
| Scene 框架 | `world_gal_game/scenes/base.py` |
| 插件 registry / manager | `world_gal_game/plugins/{registry,manager,manifest,context}.py` |
| 內建 effect / condition | `world_gal_game/plugins/{builtin_effects,builtin_conditions}.py` |
| pack 結構分析 | `world_gal_game/dev/pack_inspector.py` |
| pack 結構編輯 | `world_gal_game/dev/pack_editor.py` |
| 引擎能力清單 | `world_gal_game/dev/capability_manifest.py` |
| 五階段自驗 | `world_gal_game/dev/self_check.py` |
| 範例插件 | `games/demo_pack/plugins/step_counter/` |
| 端到端 smoke（demo_pack 三主線） | `games/demo_pack/scripts/test_*_route.json` |

---

## 詳細路線

階段路線、缺口分析、風險權衡、文件分工、變更紀錄：[ROADMAP.md](ROADMAP.md)
