# Shops

商店附在 NPC 身上 — `characters.yaml` 的某個 NPC 多寫一個 `shop:` block，
他就變商人。和他自由對話時會出現「看貨」按鈕，按下後開啟 ShopScene。

## 設定

```yaml
# content/characters.yaml
- id: cafeteria_aunty
  name: "餐廳阿姨"
  role: "學餐 / 小吃部"
  portrait: assets/characters/cafeteria_aunty.png
  description: "..."
  persona: "..."
  llm_brain: true
  shop:
    currency: money           # 哪種貨幣（resources.yaml 裡的 id）
    buy_back_ratio: 0.5       # 回收價 = item.value * 這個比率；0 = 不回收
    greeting: "同學欸，今天要呷啥？"
    listings:
      - {item: rice_box,     price: 80, stock: -1}         # -1 = 無限
      - {item: jasmine_tea,  price: 60, stock: -1}
      - {item: espresso,     price: 70, stock: 5}          # 限量
      - {item: study_notes,  price: 50, stock: -1, requires_flag: knows_study_tips}
```

`requires_flag` 在 listing 上：該 flag 為真才會顯示此商品（前置條件 / 解鎖）。

## 開啟商店的兩條路徑

### 1. 自由對話內的「看貨」按鈕

NPC 出現在當前地點時，玩家：
- 點 NPC card → 進自由對話
- 對話畫面右上的「看貨」按鈕（只有當 `shop:` 設定存在時才出現）→ 開啟 ShopScene

### 2. 從場景對白主動觸發

```yaml
effects:
  # 用 buy_item / sell_item 直接做交易，不開 UI：
  - {kind: buy_item,  target: rice_box, stat: money, value: 80}
```

或者透過 Python 端：

```python
app._open_shop("cafeteria_aunty")
```

## 用 buy_item / sell_item 直接成交

最低成本的「在場景內賣東西」做法 — 不開 ShopScene、純宣告：

```yaml
choices:
  - id: buy_lunch
    text: "「給我一個便當。」($80)"
    requires:
      - {kind: resource_gte, target: money, value: 80}
    effects:
      - {kind: buy_item, target: rice_box, stat: money, value: 80}
```

`buy_item` 的語義：
- 從玩家手上拿走 `value` 數量的 `stat` 貨幣
- 加 1 個 `target` 物品進 inventory
- 預設貨幣是 `money`

`sell_item` 是反過來：
- 拿走 1 個 `target` 物品
- 加 `value` 數量的 `stat` 貨幣（沒寫 value 用 `item.value`）

## 賣不出去的東西

`Item.locked = true` 的東西 ShopScene 的「賣出」欄不會顯示。
通常用在「故事關鍵物品」「主角的便當盒（NPC 送的紀念品）」等。

## 多貨幣商店

不同 NPC 可以收不同貨幣：

```yaml
- id: shrine_priest
  name: "巫女"
  shop:
    currency: faith         # 用「信仰點數」買東西
    listings:
      - {item: amulet, price: 5}
      - {item: omikuji, price: 1}
```

一個 NPC 只有一個貨幣 — 如果你的店要同時收兩種貨幣，目前的做法是用兩個 NPC
（或進階：寫個 scene 直接呼叫多次 `spend_resource`）。

## 進階：限時 / 隨機商品

引擎不直接內建「每天 reset 庫存」這種概念，但你可以用 scene + effect 拼出來：

```yaml
# scenes/daily_restock.yaml
scenes:
  - id: daily_restock
    title: "（隱藏：每天早上補貨）"
    location: cafeteria
    requires:
      - {kind: time_in, value: [morning]}
    lines: []                        # 空場景
    on_end:
      - {kind: set_flag, target: cafeteria_restocked_today}
      # 真正的補貨目前要寫 Python — 未來會做 effect kind `restock_shop`。
```

未來會加 `restock_shop` 這類 effect。如果你急著要，可以在 `world_gal_game/core/shop.py`
的 `Shop` 上加一個 method，並在你的 game pack 的 `entry.py` 裡 hook 進去。

## UI 細節

ShopScene 的兩欄：

```
+-----------------------------------------------+
| 商店 · 餐廳阿姨               [關閉 Esc]      |
| 持有: $500 錢包  同學欸，今天要呷啥？          |
|                                               |
| 買入                  | 賣出                   |
| [便當     $80 [購買]] | [茉莉花茶 +$30 ×1 [賣出]]|
| [茉莉花茶 $60 [購買]] |                        |
| ...                  |                        |
+-----------------------------------------------+
```

- 餘額不足的買入列表項：按鈕變灰、價格變紅
- 賣出列只顯示玩家持有 + 非 locked + `buy_back_ratio` 算出的價 > 0 的物品
