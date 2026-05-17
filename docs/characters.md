# Characters

人物住在 `content/characters.yaml`。每個 NPC 一個 dict；
其中可選的 `shop` 子物件讓他變商人。

> **LLM brain 已 deferred 到 v2**：本版本沒有 LLM 驅動的自由對話。
> 所有 `llm_brain` / `llm_speaker` / `llm_directive` 欄位都保留下來（會被解析、
> 不會報錯），但執行時：
>
> - 標 `llm_speaker: true` 的對白 line 會顯示它的 `text:` fallback；
> - 點 NPC card 會開出 **NPC 行動 overlay**（送禮 / 看貨），不是自由對話。
>
> 將來 v2 接回 ClaudeBrain 時，現有 YAML **完全不用改**；點 NPC 會多一個
> 「自由對話」按鈕。

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

  # 人物素材（給玩家在好感 / 事件記錄看；未來 LLM 重接時也會用）
  description: |
    外觀 / 氣質 / 第一眼印象。
  persona: |
    說話風格、不同情緒下的反應、看待玩家的方式。
  voice: "說話節奏與口氣（一句話）。"
  backstory: |
    過去 — 玩家還不知道的事。
  secrets:
    - "她其實是某段傳說的轉世。"
  likes:    ["古典文學", "雨天的圖書館"]
  dislikes: ["拍照"]
  safe_topics: ["宋詞", "茉莉花茶"]

  # 影響地點與場景觸發
  affiliated_location: library
  associated_ghost_story: "藏書姑娘"

  # 好感度系統
  thresholds:
    - {name: "朋友", value: 25, unlocks: ["qingyi_friend_mode"]}
    - {name: "戀人", value: 100, unlocks: ["qingyi_ending_good"]}

  # v2 預留欄位：當 LLM 接回時生效，目前被忽略。
  llm_brain: true
  llm_model_hint: claude-haiku-4-5

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

## 點 NPC 後做什麼（v1）

點 NPC card 開出 NPC 行動 overlay：

- **送禮** — 開啟 inventory picker，選一個物品 → 套用 `gift` effect（影響好感）
- **看貨** — 若 NPC 有 `shop:`，開 ShopScene
- **離開** — 關閉 overlay

v2 接回 LLM 後會多一個「自由對話」按鈕。

## NPC 短期記憶（dormant）

`NPC.memory.events` 欄位保留在 NPC model 上，會持久化到存檔。
v1 不主動寫入；v2 重接 LLM 後，每次自由對話會 push 一條記憶。
