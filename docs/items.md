# Items

物品系統涵蓋三類用法：
1. **禮物** — 送給 NPC，自動依 likes/dislikes 算好感變化
2. **消耗品** — 「使用」掉它，套用一連串 effects
3. **故事 / 關鍵物品** — 純當作 condition 的 has_item 用

## 宣告物品

`content/items.yaml`：

```yaml
items:

  # 1) 純禮物
  - id: jasmine_tea
    name: "茉莉花茶"
    description: "她最喜歡的味道。"
    icon: assets/ui/item_tea.png
    category: gift           # consumable | gift | key | quest | material | wearable | misc
    matches_tags: ["茉莉花茶"]   # 對應 NPC.likes / NPC.dislikes 的標籤
    value: 60                # 預設賣價（在預設貨幣下）
    tags: ["drink"]

  # 2) 消耗品
  - id: rice_box
    name: "便當"
    description: "餐廳阿姨手工的紅燒肉便當。"
    icon: assets/ui/item_ricebox.png
    category: consumable
    consumable: true
    value: 80
    use_effects:                          # 「使用」時逐個 apply
      - {kind: gain_resource, target: energy, value: 25}
      - {kind: log_event, target: "吃了一份便當。"}

  # 3) 關鍵物品 — 不可賣、不可丟、要的時候才出現
  - id: special_key
    name: "特藏書庫的鑰匙"
    description: "圖書館阿姨偷偷塞給你的。"
    category: key
    locked: true              # 拒絕 sell_item / drop / gift

  # 多貨幣 / 限制堆疊
  - id: gem
    name: "寶石"
    value: 100
    prices: {gold: 100, faith: 1}  # 在不同貨幣下不同價格
    max_stack: 5                    # 最多疊 5 個
    rarity: "rare"
    tags: ["valuable"]
```

完整欄位：

| 欄位 | 說明 |
|---|---|
| `id` / `name` / `description` / `icon` | 基本 |
| `category` | `consumable` / `gift` / `key` / `quest` / `material` / `wearable` / `misc` — 引擎不強制，只是分類用 |
| `consumable` | `true` 才允許 `use_item` 消費 |
| `use_effects` | list of effect — 使用時依序套用 |
| `value` | 預設賣價（被 `sell_item` 引用） |
| `prices` | `{currency_id: price}` — 蓋過 value |
| `tags` | 自由分類 |
| `rarity` | 自由分類 |
| `max_stack` | None = 無限疊；數字 = 上限 |
| `stackable` | false 時每個一格（未來分格 UI 會用到） |
| `consumed_on_gift` | 預設 true，送禮會消耗 |
| `gift_modifier` | `{npc_id: delta}` 顯式蓋過好感計算 |
| `matches_tags` | NPC likes / dislikes 比對的 tag |
| `locked` | true 不能 sell / drop / gift |

## 在場景操作

### 給 / 收 / 用

```yaml
effects:
  - {kind: give_item, target: jasmine_tea, value: 1}   # +1 罐茉莉花茶
  - {kind: give_item, target: jasmine_tea}             # value 不寫，預設 1
  - {kind: take_item, target: jasmine_tea, value: 1}   # -1
  - {kind: use_item,  target: rice_box}                # 消耗 + 套用 use_effects
```

### 送禮

```yaml
# 從場景 effect:
effects:
  - {kind: gift, target: qingyi, stat: jasmine_tea}    # stat 欄位放 item_id
```

- `target` = 收禮的 NPC id
- `stat`   = 物品 id（借用這個欄位，因為它是字串）
- `value`  = 數量，預設 1

好感變化：
1. `item.gift_modifier[npc.id]` 有定義 → 用它
2. `item.matches_tags ∩ npc.likes` 有交集 → +8
3. `item.matches_tags ∩ npc.dislikes` 有交集 → -5
4. 都沒有 → +2（送禮這個動作本身有意義）

`item.consumed_on_gift = true`（預設）會在送禮後從 inventory 刪掉。

### 商店買賣（細節見 [shops.md](shops.md)）

```yaml
- {kind: buy_item,  target: rice_box, stat: money, value: 80}   # 花 $80 買 1 個便當
- {kind: sell_item, target: rice_box, stat: money, value: 40}   # 賣 1 個拿 $40
```

`sell_item` 的 `value` 不寫時用 `item.value`。

## 在條件查詢

```yaml
requires:
  - {kind: has_item, target: jasmine_tea}            # 至少 1 個
  - {kind: has_item, target: jasmine_tea, value: 2}  # 至少 2 個
```

## 起始持有物

`meta.yaml`：

```yaml
starting_inventory:
  jasmine_tea: 1
  espresso: 1
  rain_recording: 1
```

開新遊戲時直接加進 inventory。受 `max_stack` 限制。

## 為什麼某 item 沒進 inventory？

兩個常見原因：
1. `max_stack` 已經滿了 — 引擎安靜地不超過上限。
2. item id 拼錯了 — 引擎仍會把它加進 inventory，但 UI 顯示「未在 items.yaml 註冊」。
   去 `content/items.yaml` 補上 id 即可。

## UI

- **物品 (I) overlay**：列出全部持有物品，每張卡片顯示圖示、名稱、敘述、數量。
- **送禮選擇器**：在自由對話畫面點「送禮」會打開 InventoryScene 的 picker 模式 —
  點一個物品就送出。
- **使用按鈕**：對 `consumable: true` 的物品，物品 overlay 會多一個「使用」按鈕。

## 跟成就 / 資源的搭配

物品 use_effects 可以呼叫所有 effect kind：

```yaml
- id: lucky_charm
  name: "幸運符"
  consumable: true
  use_effects:
    - {kind: gain_resource, target: faith, value: 1}
    - {kind: affection, target: shrine_keeper, value: 3}
    - {kind: set_flag, target: charm_used_today}
```
