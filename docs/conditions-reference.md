# Conditions — Full Reference

Condition 是「判斷一件事是否成立」的宣告。
用在 `Choice.requires` / `Choice.forbids` / `Line.requires` /
`Scene.requires` / `SceneHook.requires_flags` 等地方。

所有 condition 長這樣：

```yaml
{kind: <kind>, target: <id>, value: <number / string / list>, stat: <optional>}
```

簡寫字串：

```yaml
"flag:met_qingyi"                        # = {kind: flag, target: met_qingyi}
"affection_gte:qingyi=50"                # = {kind: affection_gte, target: qingyi, value: 50}
```

---

## 旗標（flags）

### `flag` — flag 為真

```yaml
- {kind: flag, target: met_qingyi}
```

`flag` 真假的定義：set 過、且當前值是 truthy（不是 `False` / `0` / 空字串）。

### `not_flag` — flag 為假（或未設）

```yaml
- {kind: not_flag, target: ending_qingyi}
```

### `flag_eq` — flag 等於某個值

```yaml
- {kind: flag_eq, target: visit_count, value: 3}
- {kind: flag_eq, target: stance, value: "loyal"}
```

對非 bool flag 特別有用（counter、enum-like 字串…）。

---

## 好感度

### `affection_gte` — 好感 >= value

```yaml
- {kind: affection_gte, target: qingyi, value: 50}
- {kind: affection_gte, target: qingyi, value: 30, stat: trust}
```

`stat` 預設 `"affection"`。

### `affection_lt` — 好感 < value

```yaml
- {kind: affection_lt, target: qingyi, value: 0}     # 互相討厭時的劇情
```

---

## 時段 / 進度

### `time_in` — 當前 time_of_day 在 value 列表內

```yaml
- {kind: time_in, value: [evening, night, midnight]}    # 夜晚場景
- {kind: time_in, value: [noon]}                         # 中午限定
```

`value` 是字串 list，元素是：`morning · noon · afternoon · evening · night · midnight`。

### `visited` — 玩家曾經到過某地點

```yaml
- {kind: visited, target: library_stacks}
```

只要進去過一次就永遠成立。

### `scene_played` — 場景曾經播過

```yaml
- {kind: scene_played, target: meet_qingyi}
```

成為條件鎖的常見用法。

---

## 物品

### `has_item` — 持有至少 N 個物品

```yaml
- {kind: has_item, target: jasmine_tea}                 # 至少 1
- {kind: has_item, target: jasmine_tea, value: 3}       # 至少 3
```

---

## 成就

### `achievement` — 成就已解鎖

```yaml
- {kind: achievement, target: ach_first_step}
```

---

## 資源

### `resource_gte` — 資源 >= value

```yaml
- {kind: resource_gte, target: money,  value: 100}
- {kind: resource_gte, target: energy, value: 50}
```

### `resource_lt` — 資源 < value

```yaml
- {kind: resource_lt, target: energy, value: 30}        # 體力低時的特殊對白
```

### `resource_eq` — 資源 == value

```yaml
- {kind: resource_eq, target: faith, value: 0}
```

---

## 組合語義

- **requires**: list of condition，**全部成立**才會啟用 / 觸發
- **forbids**: list of condition，**任何一個成立**就會擋掉

```yaml
choices:
  - id: pay
    text: "「我請。」($50)"
    requires:
      - {kind: resource_gte, target: money, value: 50}
      - {kind: not_flag, target: angry_at_someone}
    forbids:
      - {kind: flag, target: bankrupt}
```

語意：錢夠 + 沒生氣，且沒破產 → 才能選。

---

---

## 任務（Quest）

### `quest_active` — 任務進行中

```yaml
- {kind: quest_active, target: find_ghost_book}
```

quest 目前是 `active` 時成立。`inactive`、`completed`、`failed` 都是 false。

### `quest_completed` — 任務已完成

```yaml
- {kind: quest_completed, target: find_ghost_book}
```

quest 狀態為 `completed` 時成立。

### `objective_completed` — 某個目標已完成

```yaml
- {kind: objective_completed, target: find_ghost_book, stat: visit_stacks}
```

`target` = quest id，`stat` = objective id。
只要該 objective 被標記完成，不論 quest 整體狀態為何都成立。

---

## 加新 condition kind

編輯：
1. `world_gal_game/core/story_graph.py` 的 `Condition.kind` Literal
2. `world_gal_game/core/game_state.py` 的 `evaluate()` 加新 branch

寫個 pytest 鎖死它 — `tests/test_game_state.py` 有現成模板可抄。
