# Effects — Full Reference

Effect 是「我要改變遊戲狀態」的宣告。所有 effect 都長這個樣子：

```yaml
{kind: <kind>, target: <id>, value: <number / string / bool>, stat: <optional>}
```

或用簡寫字串：

```yaml
"affection:heroine_1=5"       # = {kind: affection, target: heroine_1, value: 5}
"set_flag:met_heroine_1"      # = {kind: set_flag, target: met_heroine_1}
```

下面按主題分組，每個 kind 都附完整範例 + 行為說明 + 邊界情況。

> **本表列的是引擎內建 kind**。插件可以註冊新 kind 加進同一份 registry —
> 跑 `wgg capabilities --pack <pack>` 或 `wgg capabilities --format json` 就能
> 拿到該 pack 載入時實際可用的全套清單（含 plugin 提供的）。寫插件的方法見
> [plugins.md](plugins.md)。

---

## 狀態變更

### `affection` — 調整好感度

```yaml
- {kind: affection, target: heroine_1, value: 5}               # +5 affection
- {kind: affection, target: heroine_1, value: -3, stat: trust} # -3 trust
```

`stat` 預設 `"affection"`。
回傳 `{kind, target, new, unlocked: [threshold names]}`。
跨越好感度門檻時，`unlocked` 會列出該門檻 unlocks 的字串。

### `stat` — 同 `affection`，但語意上指其他多軸 stat

```yaml
- {kind: stat, target: heroine_1, value: 8, stat: fear}
```

只是 `affection` 的別名 — 預設 stat 變成必填。

### `set_flag` — 設旗標

```yaml
- {kind: set_flag, target: prologue_done}                      # = true
- {kind: set_flag, target: count, value: 7}                    # 任意值
```

### `set_flag_if_unset` — 只在旗標尚未設定時設值

```yaml
- {kind: set_flag_if_unset, target: first_met_heroine, value: qingyi}
```

如果 `target` 已經是 truthy，這個 effect 不會覆蓋原值。適合記錄「第一次遇見誰」這類只應寫入一次的狀態。

### `increment_flag` — 加減旗標的整數值

```yaml
- {kind: increment_flag, target: visit_count, value: 1}        # +1
- {kind: increment_flag, target: visit_count, value: -1}       # -1
```

### `log_event` — 加一則自訂事件記錄

```yaml
- {kind: log_event, target: "你在湖邊聽見了陌生的歌聲。"}
- {kind: log_event, target: "標題", value: "副標題（可選）"}
```

寫進 EventLog 的 kind = "custom"，會出現在事件記錄 overlay。

---

## 流動 / 場景控制

### `advance_time` — 推進時段

```yaml
- {kind: advance_time, value: 1}     # 推進 1 個時段
- {kind: advance_time, value: 3}     # 例如：早晨 → 中午 → 下午 → 傍晚
```

時段順序：morning → noon → afternoon → evening → night → midnight → 下一天 morning。

### `move_to` — 移動玩家到一個地點

```yaml
- {kind: move_to, target: park}
```

只是改 `state.map.current_location_id`，不會觸發 enter scene_hook。
要觸發 hook 用兩段：`move_to` 然後 `play_scene`。

### `unlock_location` — 設置 `unlock:<location>` 的 flag

```yaml
- {kind: unlock_location, target: secret_basement}
```

對 location 的 `requires_flags: [unlock:secret_basement]` 解鎖。

### `play_scene` — 接到下一個場景

```yaml
- {kind: play_scene, target: ending_lover}
```

最常用在 `on_end:` 把多個場景串成 cutscene。`Choice.next_scene` 是它的
語法糖。

### `end_scene` — 強制立刻結束本場景

```yaml
- {kind: end_scene}
```

幾乎用不到 — 場景本來就會在所有 line / choice 跑完後自然結束。
特殊情況：在 line 的 `effects:` 裡用，提早結束本場景。

---

## 物品

### `give_item` — 加入 inventory

```yaml
- {kind: give_item, target: jasmine_tea}                       # +1
- {kind: give_item, target: jasmine_tea, value: 3}             # +3
```

受 `item.max_stack` 限制。

### `take_item` — 從 inventory 拿走

```yaml
- {kind: take_item, target: jasmine_tea}                       # -1
- {kind: take_item, target: jasmine_tea, value: 2}             # -2
```

回傳 `removed: true/false`。沒夠不會強拿。

### `use_item` — 消耗一個物品 + 套用 use_effects

```yaml
- {kind: use_item, target: rice_box}
```

需要 `item.consumable: true`。會把 item 的 `use_effects` 逐個套用。
回傳 `{kind, item, effects: [sub_results]}` 或 `{kind, error}`。

### `gift` — 把物品送給 NPC

```yaml
- {kind: gift, target: heroine_1, stat: jasmine_tea}
```

`target` = NPC id，`stat` = item id（借用這個欄位）。
好感變化邏輯見 [items.md](items.md)。

---

## 資源（金錢、體力、學分、信仰…）

### `gain_resource` — 加

```yaml
- {kind: gain_resource, target: money,  value: 100}
- {kind: gain_resource, target: energy, value: -20}   # 也可以 -
```

### `spend_resource` — 減（但餘額不足時不會扣）

```yaml
- {kind: spend_resource, target: money, value: 50}
```

餘額不足 → 回 `{kind, error: "insufficient", needed, balance}`，**錢不動**。
跟 `gain_resource` 的 `-50` 不同（後者會直接讓餘額變負，受 `min` 限制）。

### `set_resource` — 設絕對值

```yaml
- {kind: set_resource, target: energy, value: 100}
```

---

## 商店

### `buy_item` — 花貨幣換物品

```yaml
- {kind: buy_item, target: rice_box, stat: money, value: 80}
```

`target` = item id，`stat` = currency id，`value` = price。
餘額不足會回 `{error: "insufficient_funds"}`。

### `sell_item` — 用物品換貨幣

```yaml
- {kind: sell_item, target: jasmine_tea, stat: money, value: 30}
- {kind: sell_item, target: jasmine_tea, stat: money}        # value 不寫，用 item.value
```

`item.locked = true` 的東西不能賣（會回 `{error: "missing item"}` —
這個 error message 未來會改成 "locked"）。

---

## Result 結構

每個 effect 套用後都會回一個 dict：

```python
{"kind": "<effect kind>",
 "target": ...,                    # 通常有
 "new": ...,                       # 新值（資源 / 好感）
 "unlocked": [...],                # 好感門檻名（如果跨越了）
 "error": "...",                   # 失敗時
 "...其他依 kind 而定..."}
```

`apply_all(effects)` 回 list of result（按 effects 順序）。

---

## 設計提示

**1. 把整組 effect 包成 on_end 而不是 line.effects**：
這樣場景被中途打斷（玩家退到標題）時，這組 effect 不會殘留半套用狀態。

**2. 使用 condition 守護 effect**：

```yaml
choices:
  - id: pay_full
    text: "「全包了。」($200)"
    requires:
      - {kind: resource_gte, target: money, value: 200}
    effects:
      - {kind: spend_resource, target: money, value: 200}
      - {kind: affection, target: someone, value: 10}
```

不寫 condition 的話，`spend_resource` 會在錢不夠時靜默失敗，
但好感還是 +10 — 不合理的搭配。

**3. effect 順序很重要**：

```yaml
on_end:
  - {kind: set_flag, target: arc_done}        # 先設 flag
  - {kind: play_scene, target: ending}         # 再接結局 — ending 的 requires 可以查 arc_done
```

**4. 加新 effect kind 的方式**：
編輯 `world_gal_game/core/story_graph.py` 的 `Effect.kind` Literal +
`world_gal_game/core/game_state.py` 的 `apply()` 加新 branch。
寫一個 pytest 鎖死它。

---

## 任務（Quest）

### `start_quest` — 啟動任務

```yaml
- {kind: start_quest, target: find_sketchbook}
```

把 quest 從 `inactive` → `active`。若 quest 已不是 `inactive`，靜默無效。
回傳 `{kind, quest, started: True/False}`。

### `complete_objective` — 完成一個目標

```yaml
- {kind: complete_objective, target: find_sketchbook, stat: obj_square}
```

`target` = quest id，`stat` = objective id。
標記指定 objective 完成。若所有 non-optional objectives 全部完成，
自動把 quest 升為 `completed`（`auto_completed: true`）。
回傳 `{kind, quest, objective, ok, auto_completed}`。

### `complete_quest` — 直接完成任務

```yaml
- {kind: complete_quest, target: find_sketchbook}
```

不管 objectives 狀態，直接把 quest 設為 `completed`。
適合「劇情強制結束」的情況。
回傳 `{kind, quest, done: True/False}`。

### `fail_quest` — 任務失敗

```yaml
- {kind: fail_quest, target: find_sketchbook}
```

把 quest 設為 `failed`。已 `completed` 的 quest 無效（回 `failed: False`）。
回傳 `{kind, quest, failed: True/False}`。
