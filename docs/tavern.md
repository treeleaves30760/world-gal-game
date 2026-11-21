# 酒館模式（Tavern）：角色卡、多 agent 排演、去 AI 味

酒館模式把「酒館」式角色扮演前端的做法搬進引擎，用來**寫對白**：

- 每個角色有一張**角色卡**，寫的是「她怎麼講話」，不是「她是什麼個性」。
- 一場戲由**多個 agent 各演一個角色**排出來，而不是一個模型一口氣寫完所有人的台詞。
- 每一句寫完當場過**去 AI 味檢查**，不合格就附上原因退回給寫的那個 agent 重來。
- 排完的結果是一個普通的場景（`content/scenes/` 裡的 YAML），遊戲執行時不需要任何模型。

> **階段性成果（2026-11）。** 這份文件只描述已經做好的部分。還沒做的列在
> [限制與尚未完成](#限制與尚未完成)，後續計畫見 [ROADMAP](../ROADMAP.md)。

---

## 為什麼要這樣做

一個模型同時寫所有角色，會有固定的毛病：每個人都知道所有事、都想讓這場戲順利、都把
問題回答完整、講話都是同一個口氣。這不是換提示詞能解決的，因為作者只有一個。

酒館模式的做法是把作者拆開：

| 問題 | 做法 |
|---|---|
| 所有角色共用一個腦袋 | 每個角色一個 agent，只拿得到自己的卡、自己的祕密、自己這場戲想要的東西 |
| 個性用形容詞描述，演出來很平 | 角色卡以**範例對話**為主，模型模仿句子的節奏比照著形容詞演準得多 |
| 指令寫在開頭，後面就忘了 | 寫法規則和這一輪的提示放在對話**最後面** |
| 套話、說破情緒、諮商師口吻 | 寫完用 linter 抓，抓到就**退回重寫**；不把一長串禁詞塞進提示詞 |
| 又要寫台詞又要標表情、選立繪 | 寫的人只寫一個自由的情緒詞，引擎事後對到實際有的立繪 |

---

## 快速開始

`demo_pack` 附了一張角色卡、一份 persona、一本世界書和一份場景簡報，可以直接試：

```bash
# 每個角色實際生效的卡（有沒有卡片檔、幾句範例、幾個祕密）
uv run world-gal-game tavern cards demo_pack

# 替整個 pack 的對白打「AI 味」分數
uv run world-gal-game tavern lint demo_pack

# 某個角色在一場戲裡實際會收到的提示詞（提示詞檢查器）
uv run world-gal-game tavern prompt demo_pack --as heroine_1 \
    --brief games/demo_pack/content/tavern/briefs/park_bench.yaml --full

# 不接模型，用離線的 echo 後端把整條流程跑一遍（預設是 dry run，只印 diff）
uv run world-gal-game tavern enact demo_pack --backend echo \
    --brief games/demo_pack/content/tavern/briefs/park_bench.yaml
```

`echo` 後端只會重播角色卡裡的範例句，用來確認流程，不是寫作。真正排戲要接模型，見
[後端與連線](#後端與連線)。

---

## 檔案配置

全部放在 `<pack>/content/tavern/`，每個檔案都是選用的：

```
content/tavern/
    cards/<npc_id>.yaml|.json|.png   角色卡（酒館的卡片可以直接丟進來）
    persona.yaml                     玩家這一邊：{{user}} 是誰
    lorebook.yaml|.json              這個 pack 的世界書
    style.yaml                       寫作風格與禁詞的覆寫
    presets/<name>.yaml|.json        提示詞預設（default.* 是預設值）
    expression_memo.yaml             記住的「情緒詞 → 立繪」對應
    briefs/<scene_id>.yaml           場景簡報
```

什麼都不放也能用：每個 NPC 會從 `characters.yaml` 的欄位自動產生一張卡，並使用引擎
內建、對應 pack 語言的預設與風格指南。單一檔案壞掉不會讓整個 pack 的工具失效，問題
會列在 `tavern cards` 的 `issues` 裡。

---

## 角色卡

角色卡相容社群的 Character Card 規格（V1 平面 JSON、V2 `chara_card_v2`、V3
`chara_card_v3`，以及把卡片內嵌在 PNG 文字區塊的格式）。引擎另外加了幾個欄位，匯出成
酒館格式時收在 `extensions.world_gal_game` 裡，來回不會掉。

卡片檔只需要寫 `characters.yaml` 寫不出來的東西，其餘欄位（`description` / `persona` /
`voice` / `backstory` / `secrets` / `likes` / `dislikes`）會自動帶進來：

```yaml
# content/tavern/cards/heroine_1.yaml
name: 林清雪
talkativeness: 0.4            # 自然順序下開口的機率

speech:
  diction: 口語、偏慢。講到畫會變具體，講到自己就變短。
  sentence_length: 多半十個字上下。緊張時句子會斷掉，不會變長。
  particles: [吧, 欸, 啦]
  habits:
    - 被稱讚的時候不接話，改講別的。
    - 不想答的問題用反問擋回去。
  when:
    緊張: 話變少，句子只講一半。
  never_says: [我很感動, 謝謝你願意]

secrets:                      # 只有扮演她的 agent（和導演）看得到
  - 素描本最後幾頁畫的是{{user}}。

affection_voices:             # 好感度到哪裡，講話方式就換成哪一條
  - {min: 0, note: "客氣、保持距離。"}
  - {min: 25, note: "開始會吐槽對方。"}

expression_aliases:           # 自由情緒詞 → 實際有的立繪
  shy: [害羞, 難為情, 不好意思]

mes_example: |
  <START>
  {{user}}: 妳畫得很好欸。
  {{char}}: 「你吃過午餐了嗎？」
  <START>
  {{user}}: 找不到的話怎麼辦？
  {{char}}: 「那就……」
  {{char}}: 「再畫一本吧。大概。」
```

寫卡的要點：

- **範例對話最重要。** 寫她「會怎麼閃」，不要寫她「是什麼個性」。五到十句、不同情境。
- `speech` 的內容會以「偶爾才用」的方式放進提示詞。把口頭禪寫成規則，模型會每句都用。
- `never_says` 同時是 linter 的規則：這個角色說了這些話會被退回。
- `secrets` / `knowledge` / `goals` 是**私有**的，見下面的資訊分隔。

完整欄位（含酒館規格的 `first_mes`、`system_prompt`、`post_history_instructions`、
`character_book`、`depth_prompt` 等）見 `world_gal_game/tavern/card.py` 的
`CharacterCard`。

### 匯入與匯出

```bash
# 酒館的卡片（.png / .json / .yaml）→ content/tavern/cards/<id>.yaml
uv run world-gal-game tavern card-import <pack> some_card.png --as heroine_1

# 匯出成酒館可讀的 JSON 或 PNG
uv run world-gal-game tavern card-export <pack> heroine_1 --out card.png --spec v3
```

匯入時，卡片的名字和 pack 裡某個角色同名就會自動綁到該角色。卡片是從外面下載的內容：
引擎只把它當文字放進提示詞，載入卡片不會執行任何東西；卡片裡的巨集只能讀寫酒館自己的
變數，碰不到遊戲狀態。

### persona

`persona.yaml` 是玩家這一邊。`description` 是別的角色看得到的部分；`speaker` 決定排出
來的場景裡主角台詞掛誰的名字（預設 `{player_name}`）。

---

## 世界書

世界書是「對話裡提到關鍵字時，才放進提示詞」的背景知識。支援關鍵字、次要關鍵字與
AND / NOT 邏輯、常駐條目、機率、掃描深度、遞迴觸發、互斥群組、token 預算，以及酒館
世界書檔和卡片內嵌世界書兩種格式。

引擎多了一條規則：條目可以用和場景相同的條件**跟著劇情走**。

```yaml
# content/tavern/lorebook.yaml
entries:
  - name: 小鎮
    constant: true
    content: 故事發生在一個走路就能逛完的小鎮。
  - name: 素描本找回來了
    keys: [素描本, 本子]
    requires: ["flag:quest_done"]
    content: 素描本已經回到清雪手上。最後幾頁她還沒讓任何人看過。
```

每個條目這一輪為什麼進了提示詞、為什麼沒進（沒命中、次要關鍵字不符、機率沒中、超出
預算、被狀態條件擋掉），提示詞檢查器都會列出來。

---

## 預設與提示詞檢查器

**預設**是一份有順序的區塊清單，決定提示詞怎麼組：哪些區塊、什麼順序、什麼角色，以及
區塊是放在固定位置，還是插在對話歷史裡「從後面數第幾則」。引擎讀得懂酒館的 Chat
Completion 預設（`prompts` + `prompt_order`），放進 `content/tavern/presets/` 即可；
外來預設沒有的引擎區塊（說話方式、私有資訊、場景、表情、寫法、這一輪的提示）會自動補在
合理的位置。

支援常用的酒館巨集（`{{char}}`、`{{user}}`、`{{random::a::b}}`、`{{setvar::k::v}}`、
`{{if 條件}}…{{else}}…{{/if}}` 等）。和聊天前端不同的地方：

- **可重現**：隨機巨集用的是有種子的亂數，同樣的種子組出同樣的提示詞。
- **用遊戲裡的時間**：`{{time}}` / `{{date}}` 讀的是劇中時間，沒有提供就是空字串，
  不會把現實日期漏進故事。

`tavern prompt` 印出一個角色實際會收到的東西：

```
prompt: ~1991 tokens (estimate)
  st  tokens  role       position     block
  [+]    168  system     relative     main
  [+]    282  system     relative     speechProfile
  [+]    324  system     relative     privateKnowledge
  [ ]      0  system     relative     scenario
  ...
world book: ~118 tokens of 1024
  activated          小鎮 — constant
  state_gated        素描本找回來了 — requires flag:quest_done
  no_match           雜貨鋪老闆
```

token 數是不依賴任何供應商的估算值（中日韓文一字一個、其餘四個字元一個），用來看比例
和預算，不是計費數字。

---

## 多 agent 舞台

### 簡報

一場戲從一份簡報開始：

```yaml
# content/tavern/briefs/park_bench.yaml
id: park_bench
title: "湖畔 · 長椅"
location: park
premise: |                    # 所有人都知道的情境
  清雪一個人坐在長椅上，看到{{user}}走過來，把膝蓋上的紙翻到了背面。

setup:                        # 排戲前套用的效果：這場戲預設的旗標與好感度
  - {kind: set_flag, target: quest_started, value: true}
  - {kind: affection, target: heroine_1, value: 8}

cast:
  - id: heroine_1
    objective: 不想讓人看到她在畫什麼，可是又有一點想被問。
    tactic: 先把話題帶去別的地方。

player:
  mode: actor                 # actor：由 agent 扮演｜silent：不說話｜human：真人輸入
  objective: 想知道她剛才在畫什麼。

beats:                        # 只有導演看得到
  - goal: 清雪把紙藏起來，兩個人講的都是別的事。
  - goal: 清雪自己提起素描本，但話只說一半。
    must_include: [素描本]

ending: 停在那張被翻到背面的紙上。
order: director               # director｜natural｜round_robin

choices:                      # 機制由作者決定，字句交給舞台寫
  - id: ask_again
    intent: 再問一次她在畫什麼
    effects: [{kind: affection, target: heroine_1, value: 2}]
```

### 誰看得到什麼

| | 自己的卡與祕密 | 自己這場戲的目標 | 別人的祕密 | 節拍與結尾 | 公開的對話 |
|---|---|---|---|---|---|
| 角色 agent | 看得到 | 看得到 | 看不到 | 看不到 | 看得到 |
| 主角 agent | persona | 看得到 | 看不到 | 看不到 | 看得到 |
| 旁白 agent | — | — | 看不到 | 看不到 | 看得到 |
| 導演 agent | 全部 | 全部 | 全部 | 看得到 | 看得到 |

- **角色**只寫自己這一次開口的一兩句，外加最多一行動作。
- **導演**不寫台詞，只決定下一個開口的是誰，並給那個人一句「做得到的事」當提示
  （「不回答，反問他為什麼想知道」），不是形容詞，也不是台詞本身。
- **旁白**只寫主角看得到、聽得到的東西，不寫別人的心理。

`order: natural` 不用導演：被點到名字的人接話，否則依各角色的 `talkativeness` 擲骰
決定，亂數有種子所以可重現。

### 退回重寫

每一句寫完先過 linter。命中硬性規則（禁詞、替別人說話、格式符號、角色卡的
`never_says`），或一般規則累積到門檻，就附上原因退回給同一個 agent，最多重來
`retakes` 次（預設 2），最後取最乾淨的一版。替別人寫的台詞一律不會留下來。

### 產出

```bash
uv run world-gal-game tavern enact <pack> --brief <brief.yaml>            # dry run：印 diff
uv run world-gal-game tavern enact <pack> --brief <brief.yaml> --write    # 寫進 pack
```

結果經由 `PackEditor.add_scene` 寫入（預設 `content/scenes/_generated.yaml`，可用
`--into-file` 指定），所以會通過和其他結構化編輯一樣的 schema 驗證。新場景還需要你把它
接進遊戲（`scene_hooks`、`next_scene` 或 `play_scene`），否則 `validate` 會提示它無法
到達。

---

## 重演既有場景

對已經寫好的對白，`restage` 逐句讓該角色的 agent 用自己的話重說：

```bash
uv run world-gal-game tavern restage <pack> --scene meet_heroine                 # dry run
uv run world-gal-game tavern restage <pack> --scene meet_heroine --only-flagged  # 只改 linter 抓到的
uv run world-gal-game tavern restage <pack> --scene meet_heroine --write
```

只改文字，其他都不動：

- 一句對一句，行數、順序、說話者不變；效果、條件、`loc_id`、語音檔都留在原位。
- 寫回時用的是 `update_line`，diff 裡只會有 `text`。
- 原句裡的專有名詞（角色、地點、物品的名字）、數字、`《》` 內的字、`{token}` 與
  `[[op]]` 必須原樣保留；重寫丟掉了就退回，退回次數用完則**保留原句**。
- 預設只重寫有角色卡的說話者。旁白要加 `--narration`；主角需要 `persona.yaml`；帶
  rich-text 標記的行不動。

保留字眼只涵蓋引擎認得的名詞。一句話的意思有沒有跑掉，還是要人看過 diff 才算數。

---

## 去 AI 味：linter

```bash
uv run world-gal-game tavern lint <pack>
uv run world-gal-game tavern lint <pack> --scene some_scene --format json
uv run world-gal-game tavern lint <pack> --max-score 25      # 超過就以非零碼結束
```

分數是「每 100 行的加權命中數」，越低越好：A < 10、B < 25、C < 50、其餘 D。

| 類別 | 抓什麼 |
|---|---|
| 套話 | 嘴角上揚、眼中閃過、不易察覺、深吸一口氣、心跳漏了一拍、空氣凝固、時間靜止… |
| 句型 | 「不是…而是…」、比喻太密、輕輕／緩緩／微微用太多 |
| 說破 | 「我覺得很開心」、旁白替角色說出情緒 |
| 口吻 | 諮商師式安慰（謝謝你願意、你的感受、你並不孤單）、報告式面面俱到 |
| 節奏 | 句長太平均、沒有短回應、句句收在句號、刪節號太多 |
| 習慣 | 句句叫對方名字、同一個人老是用同樣的開頭、道謝道歉太密 |
| 聲音 | 兩個角色遮住名字後分不出來 |
| 用語 | 繁體 pack 混入簡體字、台灣背景出現大陸用語 |
| 收尾 | 場景最後幾行出現格言式總結 |
| 格式 | Markdown、表情符號、出戲說明、模型自述（硬性） |

內建繁體中文、簡體中文、英文、日文的規則；`content/tavern/style.yaml` 可以調整：

```yaml
locale: zh-Hant
region: tw                 # 設成 "" 關掉地區用語檢查
line_max_chars: 36
banned_phrases: [命運的安排]
extra_rules: [不要用敬語]
disable: [stock.trace]     # 關掉某條規則
regional_terms: {視頻: 影片}
```

這些數字是啟發式的。它們擅長找出值得再看一眼的句子，不擅長判斷一場戲好不好看；
乾淨的報告代表「沒有已知的毛病」，不代表寫得好。

---

## 表情詞與演出標籤

寫的人在台詞前用單獨一行標出情緒，寫**自由的情緒詞**就好：

```
<sprite:難為情>
「我的素描本如果還在的話……」
```

引擎依序嘗試：表情鍵或卡片上的別名 → 記過的對應（`expression_memo.yaml`）→ 詞裡包含
別名（「有點害羞」）→ 內建情緒詞庫找最接近、而且這個角色有的立繪 → 字串近似。都對不上
就沿用上一張。猜出來的結果會記下來，之後同一個詞永遠對到同一張；記錯了改
`expression_memo.yaml` 即可。

旁白可以用 `<bg:場景名>`、`<bgm:音樂名>`、`<cg:CG名>`、`<cg:off>`、`<sfx:音效名>`
換背景、音樂、CG 和音效。名字只會對到 pack 內容已經用到的素材（地點背景、場景用過的
音樂與 CG、音樂室與 CG 鑑賞的標題），對不上就丟掉並回報，不會指向不存在的檔案。

---

## 後端與連線

舞台本身不呼叫模型，它只發出「誰、該回答什麼」的請求並等待回覆。回覆從哪裡來由後端
決定：

| `--backend` | 說明 |
|---|---|
| `anthropic` | Claude，經由官方 SDK。需要 `pip install 'world-gal-game[llm]'`（或 `uv sync --extra llm`） |
| `openai` | 任何 OpenAI chat-completions 相容的端點（本機模型、其他供應商）。不是用來連 Claude 的 |
| `echo` | 離線：重播角色卡的範例句，用來測流程 |
| `relay` | 不用後端：由外層的 agent 自己一步一步回答（見下） |

設定放在 `~/.world-gal-game/tavern.yaml`（或用 `WGG_TAVERN_BACKEND` / `WGG_TAVERN_MODEL`
環境變數），**金鑰不放在 pack 裡**：

```yaml
backend: anthropic
anthropic:
  model: claude-opus-5-5          # 預設
  models: {director: claude-opus-5-5}   # 可以替各個角色位置指定不同模型
  effort: {actor: low, director: medium}
  fallbacks: true                 # 模型拒答時由伺服器端改用備援模型
openai:
  base_url: http://localhost:11434/v1
  model: your-local-model
  api_key_env: WGG_TAVERN_API_KEY # 存放金鑰的「環境變數名稱」
```

Claude 後端的憑證由 SDK 自行解析（`ANTHROPIC_API_KEY`，或已登入的設定檔）。目前的
Claude 模型不接受取樣參數，所以預設裡的 `temperature` 只對 `openai` 後端有效；Claude
用 `effort` 控制。

### relay：讓 AI 代理人自己當劇團

沒有 API 金鑰、或想讓 Claude Code / Codex 這類代理人用自己的子 agent 來演時：

```bash
# 開場：把舞台存進狀態檔，印出第一個請求
uv run world-gal-game tavern enact <pack> --brief <brief.yaml> \
    --backend relay --state /tmp/stage.json

# 回答目前的請求，取得下一個（重複直到 "done": true）
uv run world-gal-game tavern step /tmp/stage.json --submit "<回覆文字>"

# 結束後預覽或寫入
uv run world-gal-game tavern step /tmp/stage.json --result
uv run world-gal-game tavern step /tmp/stage.json --write
```

每個請求是一段 JSON：`role`（`director` / `actor` / `narrator` / `player` / `choices`）、
`speaker_id`，以及完整的 `messages`。請求彼此獨立，所以可以把同一個 `speaker_id` 的請求
固定交給同一個子 agent，讓每個角色各有一個不共用記憶的演員。資訊分隔由引擎負責：交給
某個角色的 `messages` 裡不會有別人的祕密。

---

## 把酒館的聊天記錄變成場景

```bash
uv run world-gal-game tavern import-chat <pack> chat.jsonl --scene-id from_chat --write
```

讀酒館存下來的 `.jsonl`，把每則訊息拆成對白、旁白和 `*動作*`，說話者對得上 pack 角色的
就掛上名牌並解析表情。它只重新整理結構，不改寫內容；匯入後請跑一次 `tavern lint`。

---

## 限制與尚未完成

- **Claude 與 OpenAI 相容後端尚未對真實服務做過端到端驗證。** 請求的形狀有單元測試
  （Claude 部分對照過已安裝 SDK 的參數簽名，OpenAI 部分對本機假伺服器測過），但還沒有
  用真實模型排過一場戲、評估過產出的品質。
- **遊戲內的即時對話還沒接。** `llm_speaker` 的行和點 NPC 的「自由對話」仍是原來的行為；
  酒館模式目前是**寫作期**的工具。
- **終端機的即時聊天（`tavern chat`）還沒有指令入口**，雖然舞台已經支援真人輸入的模式。
- **暖 session（NDJSON）還沒有 `tavern.*` 操作**，目前請用 CLI 的 relay 流程。
- `validate` / `self-check` 還不會檢查酒館的檔案（卡片欄位打錯要靠 `tavern cards`
  看 `issues`）。
- 新排出來的場景不會自動接進遊戲流程。
- 參考的那套前端裡，語音合成、AI 生圖、雙語對照輸出、獨立的標註模型，這裡都沒有做。

---

## 相關

- 角色的其他欄位、立繪、好感度：[characters.md](characters.md)、[affection.md](affection.md)
- 場景 YAML：[scenes.md](scenes.md)
- 結構化編輯與 AI 開發流程：[ai-developer-guide.md](ai-developer-guide.md)
- 接下來要做的：[ROADMAP](../ROADMAP.md)
