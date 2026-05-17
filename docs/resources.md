# Resources

引擎內建一個泛用的「具名整數」系統，名字隨你 — 金錢、體力、學分、
信仰點數、惡魔好感、教派貢獻度…任何要追蹤的「玩家數值」都用它。

## 宣告資源

寫在 `content/resources.yaml`（推薦）或 `meta.yaml` 的 `resources:` block：

```yaml
# content/resources.yaml
resources:
  - id: money
    name: "錢包"
    symbol: "$"
    description: "新台幣，校園裡的通用貨幣。"
    starting: 500
    min: 0
    # max: 999999  # 沒寫 = 無上限

  - id: energy
    name: "體力"
    starting: 100
    min: 0
    max: 100

  - id: knowledge
    name: "學識"
    starting: 0
    min: 0

  - id: faith
    name: "信仰"
    symbol: "✦"
    starting: 10
    min: -50
    max: 100
    icon: assets/ui/icon_faith.png
    visible: true   # false 的話不在 UI 狀態列出現（仍可被條件查詢）
    tags: ["religious"]
```

| 欄位 | 必要 | 說明 |
|---|---|---|
| `id` | ✓ | 唯一 id，effect / condition 用這個指 |
| `name` | | UI 顯示名稱 |
| `symbol` | | 前綴 glyph，例如 `$`、`¥`、`✦` |
| `description` | | 給未來的「資源說明」UI 用 |
| `starting` | | 開新遊戲時的初始值，預設 0 |
| `min` / `max` | | 鉗夾範圍，None = 無界 |
| `icon` | | UI 圖示路徑 |
| `visible` | | false 時不在 UI 狀態列；條件查詢不受影響 |
| `tags` | | 自由標籤，引擎忽略 |

## 在場景裡操作

### Effects

```yaml
effects:
  - {kind: gain_resource,  target: money,     value: 50}    # +$50
  - {kind: spend_resource, target: energy,    value: 20}    # -20 體力
  - {kind: set_resource,   target: knowledge, value: 0}     # 直接設成 0
```

`spend_resource` 在餘額不足時不會扣分、會在 result 裡回 `error: insufficient`。
要先判斷再消費就配條件：

```yaml
choices:
  - id: pay
    text: "「我請。」($50)"
    requires:
      - {kind: resource_gte, target: money, value: 50}
    effects:
      - {kind: spend_resource, target: money, value: 50}
      - {kind: gain_resource,  target: knowledge, value: 1}
```

### Conditions

```yaml
requires:
  - {kind: resource_gte, target: money,  value: 100}
  - {kind: resource_lt,  target: energy, value: 30}
  - {kind: resource_eq,  target: faith,  value: 0}
```

## UI 顯示

引擎的探索畫面頂端會依序列出 `visible: true` 的資源。
排序按宣告順序。範例：

```
第 1 天 · 週一 · 下午    $500 錢包    100 體力    0 學識        [選單 Esc]
```

換錢 / 換體力 / 換信仰時，會跳一個 toast 通知（跟成就解鎖同款 UI）。

## 設計建議

1. **金錢 = 一個資源就好**：宇宙裡只有一種貨幣 → 用 `money`，要兩種以上
   再開（例如 `gold` + `gems`）。
2. **體力是個迴圈**：當體力為 0 時要有「強制睡覺」的 scene_hook，否則
   玩家會卡死。可以這樣寫：

   ```yaml
   # content/locations.yaml — 所有地點都加一個 hook
   - id: anywhere
     scene_hooks:
       - scene_id: forced_sleep
         trigger: auto
         requires_flags: []
         # 沒辦法直接用 resource_lt 觸發 scene_hook（hook 只查 flag）。
         # 變通：場景內檢查資源、不滿足就 end_scene。
   ```

   或更乾淨：用一個 `exhausted` flag 當「體力=0」的 mirror，
   `gain_resource: energy = -X` 的下一個 scene 用 `effects` 檢查。

3. **特殊資源用 `visible: false`**：例如「業力」、「對某 NPC 的隱性好感」，
   你想追蹤但不想讓玩家直接看到。

## 跟 Affection / Flags 的差別

|  | Resource | Affection | Flag |
|---|---|---|---|
| 數值型 | int | int | 任何（bool / int / str） |
| 對象 | 全域（玩家） | per-NPC | 全域 |
| 多軸 | 不 (每個資源一個 id) | 是（每 NPC 多 stat） | 不 |
| UI 標籤 | name + symbol | 等級名稱 | 通常不顯示 |
| 適合用來表達 | 金錢、體力、學分、教派點數 | 對某 NPC 的好感、信任、恐懼 | "事件 X 發生過了" |
