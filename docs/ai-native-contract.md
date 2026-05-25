# AI-Coding-Native 契約

給**任意 Coding Agent**(Claude Code、Codex…)的單一入口:如何**完全理解**這個
引擎裡可改的東西,並**實際操控**它的狀態 — 而且走的是快速的 in-process / CLI
路徑,不是 MCP。

> **為什麼不是 MCP?** 引擎本身就是一個 Python library,Agent 本來就在同一個環境
> 執行程式碼。最快的介面是直接 import 或一支 CLI 呼叫 — 一支 script = 上千次狀態
> 操作,零 RPC 來回。MCP 真正有價值的是「可發現、有型別的工具契約」,那一點我們用
> **JSON Schema 匯出 + 自我描述的 SDK** 補足(見下),不必付出 server 來回的代價。

能力分三層:**理解 → 操控 → 信任**。本篇是 Agent 的**動詞**(apply / snapshot /
diff / trace);要在動手前先**讀懂** pack 的狀態空間與後果(變數清單、資料流、條件化
圖、規劃器、覆蓋率、常駐 NDJSON session、以及玩家 rollback),見
[ai-native-world-model.md](ai-native-world-model.md)。

---

## 一、理解層 — 知道自己在改什麼

每個 effect / condition / 內容模型都有機器可檢查的 **JSON Schema**。

```bash
# 完整能力快照(含已載入 plugin 的 kind):effects/conditions/hooks/widgets/...
uv run wgg capabilities --pack demo_pack --format json

# 只要 schema bundle(離線驗證 pack edit 用),語言無關:
uv run wgg capabilities --pack demo_pack --schema
```

`--schema` 輸出:

```jsonc
{
  "engine_version": "0.1.0",
  "effects":    { "affection": { /* JSON Schema: target/value/stat */ }, ... },
  "conditions": { "affection_gte": { ... }, ... },
  "models":     { "Effect": {...}, "Condition": {...}, "Line": {...},
                  "Scene": {...}, "Choice": {...}, "PortraitSpec": {...} }
}
```

- 每個 kind 的參數 schema 來自一個 pydantic arg model
  (`world_gal_game/plugins/effect_args.py`、`condition_args.py`)。型別**精準對應
  handler 實際行為**(例如吃 `int(eff.value or N)` 的 kind,`value` 是 `int|null`,
  所以省略 value 合法)。
- 完整人類可讀清單(自動生成、不手改):`docs/effects-reference.md`、
  `docs/conditions-reference.md`(用 `uv run python tools/gen_references.py` 重生,
  `--check` 可在 CI 偵測 drift)。
- 編輯前驗證:`uv run wgg validate <pack>` 會用這些 arg model 檢查(**warning 級**,
  不擋既有內容),並對拼錯的 kind 給 "did you mean"。`PackEditor` 在結構化編輯時
  也會對拼錯的 effect/condition kind 回 "did you mean"。

---

## 二、操控層 — 真的能動狀態

入口是 `world_gal_game.headless.HeadlessSession`(純 Python,無需 pygame 顯示):

```python
from world_gal_game.config import EngineConfig
from world_gal_game.headless import HeadlessSession

sess = HeadlessSession.open(EngineConfig(seed=42), pack="demo_pack")
sess.inspect()                       # 玩家視角的狀態快照(dict)
sess.affordances()                   # 「現在能做什麼、為什麼某選項被擋」
results = sess.run_script([...])     # 批次執行,回每步結果 + diff
sess.transcript                      # 該批次的完整 trace(見第三層)
```

### `run_script` 完整 op 表

| op | 參數 | 作用 |
|---|---|---|
| `move` | `location` | 移動到地點(觸發 enter/auto 場景) |
| `start_scene` | `scene` | 開始一個場景 |
| `next` | `count?` | 推進對白 N 行(自動跟隨轉場) |
| `choose` | `choice` | 選一個選項 |
| `chat` | `npc`, `message` | 與 NPC 自由對話(EchoBrain 確定性) |
| `advance_time` | `phases?` | 推進時間 |
| `set_flag` | `key`, `value?` | 設旗標 |
| `adjust_affection` | `npc`, `delta`, `stat?` | 調整好感 |
| `inspect` | — | 回傳當前 `inspect()` 快照 |
| **`apply`** | `effect: {kind,...}` | 套用**任意** effect(走 `GameState.apply`) |
| **`check`** | `condition: {kind,...}` | 評估**任意** condition |
| **`assert`** | 見下 | 斷言期望,`ok` 即 pass/fail |
| **`affordances`** | — | 回傳當前 action space |
| **`snapshot`** | `name?` | 存一個具名狀態快照 |
| **`restore`** | `name?` | 還原具名快照(分支探索) |

`assert` 形式:`{flag, equals?}`、`{affection, gte\|lt\|equals, stat?}`、
`{scene_played}`、`{condition: {...}}`。

`apply`/`check` 讓 Agent 用**遊戲真正在用的那個 effect/condition**驅動狀態(不再
只有 set_flag/adjust_affection);`snapshot`/`restore` 讓 Agent 做「選 A vs 選 B」的
樹狀探索、動態驗證每條 route。

### Determinism 契約

引擎核心**零 `random`**。需要隨機的 plugin/brain/effect 必須用
`state.rng()`(一個 `random.Random`)而非全域 `random`。設了
`EngineConfig(seed=...)` 後,`state.rng()` 確定性 —
**同 seed + 同 script ⇒ 同一份 state**。(有測試守住「引擎碼不得出現 uncontrolled
`import random`」這個不變量。)

---

## 三、信任 / 回饋層 — 驗證自己改對了

`run_script` 一次呼叫就回傳足以**完全理解這趟改了什麼**的資料 — 這正是 MCP
逐次 tool call 做不到的「rich one-shot batch」:

- **每步結果**:`results[i]` 帶 `op`、該 op 的回傳、以及(有變動時)結構化
  `diff`(`{"affection.characters.heroine_1.stats.affection": {"from":0,"to":7}}`)。
- **完整 trace**:`sess.transcript`(CLI 輸出的 `transcript` 欄位)是有序事件流:
  `effect`(含 result)、`line`、`choice`、`move`、`time`,每筆有遞增 `seq`。
- **assert**:把期望寫進 script,`ok` 直接告訴你過或不過。

### 端到端範例

`games/demo_pack/scripts/ai_native_demo.json` 展示
snapshot → 分支 → apply → assert → restore;用下面方式跑,輸出含 `results`(逐步
diff)與 `transcript`(trace):

```bash
uv run python -m world_gal_game.cli --headless \
    --script games/demo_pack/scripts/ai_native_demo.json --pack demo_pack
```

---

## 傳輸層(由通用到方便)

1. **檔案契約(最語言無關)**:`wgg capabilities --schema` 的 JSON Schema +
   `run_script` 回傳的 trace/diff(JSON)+ `wgg validate`。任何語言的 Agent 都能讀寫。
2. **CLI + JSON**:`wgg` 子命令(`--format json` / `--schema` / `--headless
   --script`),stdout 為乾淨 JSON(pygame banner 已抑制)。
3. **Python SDK**:`HeadlessSession`,in-process 跑,docstring 即文件。

---

## 速查

```bash
uv run wgg capabilities --pack demo_pack --schema      # 每個 kind 的 JSON Schema
uv run wgg validate <pack>                             # 編輯前驗證(warning 級 arg 檢查)
uv run python -m world_gal_game.cli --headless --inspect --pack demo_pack
uv run python -m world_gal_game.cli --headless --script <s.json> --pack demo_pack
uv run python tools/gen_references.py [--check]        # 重生/檢查 reference 文件
```

相關文件:[ai-native-world-model.md](ai-native-world-model.md)、
[ai-developer-guide.md](ai-developer-guide.md)、[headless.md](headless.md)、
[plugins.md](plugins.md)、[architecture.md](architecture.md)。
