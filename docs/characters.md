# Characters

人物住在 `content/characters.yaml`。每個 NPC 一個 dict；
其中可選的 `shop` 子物件讓他變商人，可選的 `llm_brain` 讓他被 Claude 驅動。

## 最小 NPC

```yaml
characters:
  - id: someone
    name: "某某"
    portrait: assets/characters/someone.png
    description: "她的外貌、氣質、第一眼印象。"
```

## 完整欄位

```yaml
- id: qingyi
  name: "林青衣"
  role: "中文系大三"
  age: 21
  is_heroine: true                  # 主角候選之一；UI 在好感頁特別標
  route_id: qingyi                  # 跟劇情標記 route 對應

  # 立繪 — portrait 是預設；portrait_set 按表情切換。
  portrait: assets/characters/qingyi_normal.png
  portrait_set:
    smile:   assets/characters/qingyi_smile.png
    sad:     assets/characters/qingyi_sad.png
    worried: assets/characters/qingyi_worried.png

  # 給 LLM 的 system prompt 素材
  description: |
    外觀 / 氣質 / 第一眼印象。
  persona: |
    說話風格、不同情緒下的反應、看待玩家的方式。
  voice: "說話節奏與口氣（一句話）。"
  backstory: |
    過去 — LLM 知道但玩家還不知道的事。
  secrets:                         # LLM 知道但不能直接揭露
    - "她其實是某段傳說的轉世。"
  likes:    ["古典文學", "雨天的圖書館"]
  dislikes: ["拍照"]
  safe_topics: ["宋詞", "茉莉花茶"]  # 給 LLM 提示，可以引導對話

  # 影響地點與場景觸發
  affiliated_location: library
  associated_ghost_story: "藏書姑娘"

  # 好感度系統
  thresholds:
    - {name: "朋友", value: 25, unlocks: ["qingyi_friend_mode"]}
    - {name: "戀人", value: 100, unlocks: ["qingyi_ending_good"]}

  # LLM 自由對話
  llm_brain: true                  # false = chat 用 EchoBrain（固定一句）
  llm_model_hint: claude-haiku-4-5 # 選填，覆寫 brain 預設模型

  # 自由標籤；引擎不解釋，pack 可以拿來做篩選。
  tags: ["heroine", "library"]

  # 把這個 NPC 變成商人（細節見 shops.md）
  shop:
    currency: money
    buy_back_ratio: 0.5
    greeting: "歡迎光臨！"
    listings:
      - {item: rice_box, price: 80, stock: -1}
```

詳細：
- 好感度 + 門檻 → [affection.md](affection.md)
- LLM brain → 底下
- Shop → [shops.md](shops.md)

## 立繪表情切換

場景對白裡用 `expression:`：

```yaml
- speaker: "林青衣"
  text: "「啊…謝謝你。」"
  expression: smile        # 從 NPC.portrait_set["smile"] 抓
```

`portrait:` 直接寫路徑會覆寫整個 expression 系統。

## 出沒時段

人物只在某些時段出現在某些地點 — 在 `content/locations.yaml` 的
`npcs:` block 宣告：

```yaml
- id: library
  npcs:
    - npc_id: qingyi
      times: [afternoon, evening]     # 沒寫 = 全天
      weekdays: [mon, tue, wed, thu, fri]
      requires_flags: [met_qingyi]   # 條件式出現
      forbids_flags: [qingyi_left_school]
    - npc_id: librarian
      times: [morning, noon, afternoon, evening]
```

探索畫面只會顯示「現在在這裡」的 NPC card。

## LLM Brain：讓 NPC 動態說話

設了 `llm_brain: true` 後，玩家點「自由對話」會把以下資訊組成 system prompt：

- NPC 的 description / persona / voice / backstory / secrets
- 喜歡 / 討厭
- 玩家當前的好感度
- 當前地點 + 時段
- 最近 8 條事件記錄
- NPC 的短期記憶（這個 NPC 對玩家的過去互動的私人筆記）

預設 model 是 `claude-haiku-4-5-20251001`。需要 `ANTHROPIC_API_KEY` env：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

沒設 key 時自動 fallback 到 `EchoBrain`（每次只回固定一句），
讓遊戲在離線環境仍可玩。

## 在場景對白中讓 LLM 即時生成台詞

不只自由對話，連故事場景的對白也能 LLM 化：

```yaml
- speaker: "林青衣"
  llm_speaker: true
  llm_directive: "玩家剛打翻她的咖啡，請她驚訝但體貼地回應，
                  並順便提一句『下次去學餐買新的就好』。"
  text: "（fallback：LLM 失敗時顯示這句）"
  expression: worried
```

`text` 永遠是 fallback；LLM 成功才會被覆蓋。

## NPC 短期記憶

每次自由對話結束後，引擎把該回合的（玩家輸入 / NPC 回覆 / 隱含好感變化）
推進 NPC 的 `memory.events`（環狀 buffer，預設保留 25 條）。

memory 跟著存檔走，所以「她記得你上次承諾請她喝咖啡」這種長期感是有的。
