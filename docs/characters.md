# Characters

人物住在 `content/characters.yaml`。每個 NPC 一個 dict；
其中可選的 `shop` 子物件讓他變商人。

> **用模型寫對白請看[酒館模式](tavern.md)。** 那是寫作期的工具：每個角色一張角色卡、
> 多個 agent 各演一個角色把一場戲排出來，結果寫成普通的場景 YAML。這裡的
> `description` / `persona` / `voice` / `backstory` / `secrets` / `likes` / `dislikes`
> 會自動成為該角色的角色卡內容。
>
> **遊戲執行時的即時對話還沒有接上。** `llm_brain` / `llm_speaker` /
> `llm_directive` 欄位都保留（會被解析、不會報錯），但執行時：
>
> - 標 `llm_speaker: true` 的對白 line 會顯示它的 `text:` fallback；
> - 點 NPC card 會開出 **NPC 行動 overlay**（送禮 / 看貨），不是自由對話。
>
> 之後接上時，現有 YAML 不用改；點 NPC 會多一個「自由對話」按鈕。

## 最小 NPC

```yaml
characters:
  - id: someone
    name: "某某"
    portrait: assets/characters/someone.png
    description: "她的外貌、氣質、第一眼印象。"
```

## 完整欄位

下面這份範例對應 demo_pack 的 `heroine_1`（林清雪）。`demo_pack` 目前只附一位
heroine；如果你的 pack 要多女主角，再宣告 `heroine_2`、`heroine_3`… 即可。

```yaml
- id: heroine_1
  name: "林清雪"
  role: "鎮上書店打工生"
  age: 19
  is_heroine: true                  # 主角候選之一；UI 在好感頁特別標
  route_id: heroine_1               # 跟劇情標記 route 對應

  # 立繪 — portrait 是預設；portrait_set 按表情切換。
  portrait: assets/characters/heroine_1_normal.png
  portrait_set:
    smile: assets/characters/heroine_1_smile.png
    shy:   assets/characters/heroine_1_shy.png
    sad:   assets/characters/heroine_1_sad.png

  # 人物素材（給玩家在好感 / 事件記錄看；未來 LLM 重接時也會用）
  description: |
    外觀 / 氣質 / 第一眼印象。
  persona: |
    說話風格、不同情緒下的反應、看待玩家的方式。
  voice: "說話節奏與口氣（一句話）。"
  backstory: |
    過去 — 玩家還不知道的事。
  secrets:
    - "素描本最後一頁她留著沒畫。"
  likes:    ["茉莉花茶", "素描本", "安靜的地方"]
  dislikes: ["油膩食物", "太吵的場合"]
  safe_topics: ["素描", "湖畔風景"]

  # 影響地點與場景觸發
  affiliated_location: town_square
  associated_ghost_story: "湖邊夜半的素描聲"

  # 好感度系統
  thresholds:
    - {name: "朋友", value: 25, unlocks: ["heroine_1_friend"]}
    - {name: "戀人", value: 80, unlocks: ["heroine_1_lover"]}

  # 預留欄位：遊戲內即時對話接上後生效，目前被忽略。
  # （llm_model_hint 會帶進酒館模式角色卡的 model_hint。）
  llm_brain: true
  llm_model_hint: claude-haiku-5-5

  # 自由標籤；引擎不解釋，pack 可以拿來做篩選。
  tags: ["heroine", "town"]

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

## 角色卡（酒館模式）

想讓模型寫出像這個角色會說的話，光靠上面的形容欄位不夠。在
`content/tavern/cards/<npc_id>.yaml` 補一張角色卡，寫她實際怎麼講話（範例對話、
語氣詞、迴避的話題、不會說的話）和只有她自己知道的事：

```yaml
# content/tavern/cards/heroine_1.yaml
name: 林清雪
speech:
  habits: ["被稱讚的時候不接話，改講別的。"]
  never_says: [我很感動]
secrets: ["素描本最後幾頁畫的是{{user}}。"]
expression_aliases:
  shy: [害羞, 難為情]
mes_example: |
  <START>
  {{user}}: 妳畫得很好欸。
  {{char}}: 「你吃過午餐了嗎？」
```

卡片相容酒館的 JSON / PNG 角色卡，可以直接匯入匯出。完整說明見
[tavern.md](tavern.md)。

## 立繪表情切換

場景對白裡用 `expression:`：

```yaml
- speaker: "林清雪"
  text: "「啊…謝謝你。」"
  expression: smile        # 從 NPC.portrait_set["smile"] 抓
```

`portrait:` 直接寫路徑會覆寫整個 expression 系統。

## 出沒時段

人物只在某些時段出現在某些地點 — 在 `content/locations.yaml` 的
`npcs:` block 宣告：

```yaml
- id: town_square
  npcs:
    - npc_id: heroine_1
      times: [afternoon, evening]     # 沒寫 = 全天
      weekdays: [mon, tue, wed, thu, fri]
      requires_flags: [met_heroine_1] # 條件式出現
      forbids_flags: [heroine_1_left_town]
    - npc_id: shopkeeper_uncle
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
