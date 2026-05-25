# AI-Native 世界模型

[ai-native-contract.md](ai-native-contract.md) 給了 Agent **動詞**(do)與**觀測**
(observe):apply / snapshot / restore / diff / trace。本篇補上**世界模型**
(reason)——讓 Agent 在動手之前就能理解這個 pack 的狀態空間與後果,不必把遊戲整個跑
一遍才認識它。

> 一句話:**契約讓 Agent 跑這個世界;世界模型讓 Agent 讀懂這個世界。**

沿用契約的三層(理解 → 操控 → 信任),這裡是各層新增的能力。最後一節是貫穿全篇的
設計主軸:**同一套 snapshot 機制,同時服務 Agent 與玩家。**

---

## 一、理解層 — 狀態的 schema 與資料流

### 1. 宣告式變數清單(typed state schema)

旗標過去是 `event_log.py` 裡一個 `dict[str, Any]`,第一次寫入才誕生——沒有清單、沒有
型別、沒有說明。`content/variables.yaml`(選用、向後相容)讓 pack **宣告**它的敘事
狀態:

```yaml
variables:
  - {key: ending_lover, type: bool, default: false, category: ending,
     description: "The lover ending was reached."}
  - {key: met_heroine_1, type: bool, default: false, category: progress,
     description: "Player has met heroine_1."}
```

- 模型:`world_gal_game/core/variable_spec.py` 的 `VariableSpec`
  (`key`/`type`/`default`/`description`/`category`/`values`)與 `VariableManifest`。
  `type` ∈ `bool|int|float|str|enum`;`enum` 用 `values` 界定值域。
- 載入:`content_loader.load_variables` 把 manifest 掛在 `state.meta["__variables__"]`
  (私有橋接,SaveManager 會剝除,不進存檔)。
- 列出:`wgg variables <pack>` 或 `wgg variables <pack> --check`。
- 玩家視角:`HeadlessSession.inspect()` 的 `variables` 欄位把每個宣告變數與**現值**
  併呈(`{key,type,category,description,default,value,is_set}`)——不再只是一坨無型別
  旗標。
- 編輯安全:`wgg validate <pack>` 在 `variables.yaml` 存在時做**純 YAML** 交叉檢查
  ——用到但未宣告的旗標給 "did you mean" 警告(Agent 改旗標時的拼字守門),宣告但
  從未讀寫的給提示。沒有 `variables.yaml` 的 pack 完全不受影響。

### 2. 資料流交叉引用(impact analysis)

「我改這個東西,還有誰會受影響?」過去無解——`PackInspector` 連 choice 的
`requires`/`forbids` 都不看。`world_gal_game/dev/dataflow.py` 的 `DataflowAnalyzer`
補上:

```bash
wgg inspect-pack <pack> --dataflow                 # flags/scenes/items/resources 的 writers+readers + 條件化邊
wgg inspect-pack <pack> --references ending_lover   # 單一符號的 writers+readers
```

- 對每個 flag / scene / item / resource 收集 `SymbolUsage`(writer 站點 + reader
  站點,各為人類可讀的 `Reference`,如 `scene:meet_heroine_1#line3`、
  `choice:lover_event.confess`、`ending:ending_lover`)。**讀取也掃 endings /
  achievements / clues / quests 的 `requires`**——所以「只在結局被讀」的旗標也找得到
  reader。
- 走的是 `load_pack` 後的**型別模型**,看到的與 runtime 一致。
- 額外揪出 write-only 旗標(例:demo_pack 的 `quest_done` 是 `3w / 0r`)。
- 給了 `declared_flags` 時順帶回報 `undeclared_flags` / `unused_declared_flags`。

### 3. 條件化敘事圖

`DataflowReport.edges` 是 scene→scene 的有向邊,每條帶 `guard`(gating 的
`requires`/`forbids` 條件)與 `via`(`choice`/`on_end`/`line`)。對照舊的
`PackInspector.graph()`(把每條邊都當永遠可走)——現在 Agent 能推理「**何種條件下**
一個場景通往另一個」,也是規劃器的剪枝來源。

---

## 二、操控層 — 快速控制面 + 目標導向

### 4. 常駐 NDJSON session(比 MCP 快的那條路)

契約已論證 in-process 的理由,但**語言無關的 CLI 路徑每次呼叫都重啟 Python +
重載 pack**——這跟 MCP 的 per-call RPC 是同一級成本,只是換地方付。

`wgg session` 把它解決掉:**載入一次**,然後從 stdin 逐行讀 JSON 指令、逐行寫 JSON
回應(NDJSON)。沒有 server 協議重量、沒有 schema 塞爆 context、沒有 JSON-RPC 封裝
——就是把 `run_script` 從「一次性批次」變「常駐互動」。

```bash
wgg session --pack demo_pack --seed 7
# stdin (一行一個指令,與 run_script 的 op 詞彙完全相同):
{"op":"__ping__"}
{"op":"set_flag","key":"x"}
{"op":"check","condition":{"kind":"flag","target":"x"}}
{"ops":[{"op":"start_scene","scene":"prologue"},{"op":"next","count":3}]}
{"op":"__quit__"}
```

- 每行回一行:`{"ok",...,"result"/"results","transcript"?,"seq"}`。
- 控制 op:`__ping__` / `__inspect__` / `__affordances__` / `__reset__`
  (重開 pack)/ `__quit__`。
- 壞 JSON 或引擎例外都降級成 `{"ok":false,"error":...}`,絕不終止 loop。
- 程式介面:`world_gal_game/dev/session_server.py` 的 `SessionServer.handle(line)`
  (純函式、好測)與 `run_session(pack=..., seed=...)`。

### 5. 目標導向規劃器(backward reasoning)

前向模型齊備後,Agent 不該為了測一條 route 去**手寫** `test_*.json`。
`world_gal_game/dev/planner.py` 的 `Planner` 用「確定性 session + snapshot/restore」
對 op 動作空間做 BFS(最短路),回傳抵達目標的 op 序列:

```bash
wgg plan --pack demo_pack --goal '{"flag":"quest_started"}' \
    --setup '[{"op":"start_scene","scene":"prologue"}]'
```

- 目標是 assert 形式(`{"flag":...}`、`{"scene_played":...}`、`{"affection":...,"gte":...}`、
  `{"condition":{...}}`)。
- 動作空間:`next` / 已啟用的 `choose` / `move`(`--no-moves` 關)/ `start_scene`
  (`--no-scenes` 關),以 `(location, scene, line_index, flags, played)` 去重避免
  move/next 死循環,`max_depth`/`max_nodes` 設上界。
- 回傳 `PlanResult{found, goal, path, depth, nodes_explored}`;`path` 在新 session
  上重放即達標。找不到(超出上界)回 `found=false`。

---

## 三、信任層 — 覆蓋率

### 6. 覆蓋率度量

「我的測試夠完整嗎?」`world_gal_game/dev/coverage.py` 的 `CoverageTracker` 把一次
跑過的 transcript 對照 pack 的總量:

```bash
wgg coverage games/demo_pack --script games/demo_pack/scripts/test_lover_route.json
#   scenes   6/11 (54.5%)  missing: ending_alone, ending_friend, ...
#   lines    39/67 (58.2%)
#   choices  3/9 (33.3%)
#   endings  1/3 (33.3%)  missing: ending_alone, ending_friend
```

- 四個維度(scenes / lines / choices / endings)各回 `Bucket{seen,total,pct,missing}`。
- 觀測來源:`session.transcript` 的 line/choice 事件 + `state.story.played` +
  以 `state.evaluate` 判定的已達結局。
- 配合規劃器即可「自動補洞」:覆蓋率指出缺哪條 route,規劃器算出怎麼走到。

---

## 四、設計主軸:一套機制,兩種受眾

最划算的基礎建設,是同一塊同時服務 Agent 與玩家的:

| 一塊基礎建設 | 給 Agent | 給玩家 |
|---|---|---|
| `dev/diff` 的 snapshot/restore + determinism | 分支探索、規劃器、常駐 session 的 `__reset__` | **rollback** |
| 宣告式變數清單 | 可列舉的狀態 schema + dataflow 標的 | 存檔驗證、未來 NG+/persistent data 的落點 |
| 前向模型 + 條件化圖 | 規劃器 | 真 flowchart 的資料來源 |

### 玩家 rollback

`world_gal_game/core/history.py` 的 `StateHistory` 是 `dev/diff` snapshot/restore
的有界堆疊,**和 Agent 分支探索用的是同一套機制**。對話場景在每行/每選項顯示後
`record(state, presentation)`;玩家按 **Backspace** 觸發 `rewind`——還原前一格的
state **並**重畫當時的 presentation。

關鍵設計:rollback 存的是 **(狀態快照, 已渲染的 presentation)** 配對。重畫直接用存下
的 presentation,**不重跑引擎**——否則 `_present_line` 會把那行的 effects / dialogue
ops / read-log / hooks 全部再觸發一次。effects 在該行**首次播放時**就套用了,快照已
記錄其結果;rewind 只是純視覺 + 狀態還原。

- 範圍:**場景內**(`enter()` 每場景開新的 history),不跨場景,避免跨場景重畫的複雜度。
- 開關:`EngineConfig.rollback_enabled`(預設開,進 `settings.json`)。
- 與 scrollback 區分:滾輪上/`B` 開的是唯讀文字記錄;Backspace 才真的倒轉狀態。

---

## 速查

```bash
wgg variables <pack> [--check]                       # 宣告變數清單(+ 用到未宣告/宣告未用)
wgg inspect-pack <pack> --dataflow                   # writers/readers + 條件化邊
wgg inspect-pack <pack> --references <symbol>         # 單一符號的衝擊面
wgg session --pack <pack> [--seed N]                  # 常駐 NDJSON 控制面(stdin/stdout)
wgg plan --pack <pack> --goal '<json>' [--setup '<json>']   # 目標導向路徑搜尋
wgg coverage <pack> [--script s.json]                 # scene/line/choice/ending 覆蓋率
```

相關文件:[ai-native-contract.md](ai-native-contract.md)、[headless.md](headless.md)、
[ai-developer-guide.md](ai-developer-guide.md)、[architecture.md](architecture.md)。
