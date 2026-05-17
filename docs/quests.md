# Quests

Quest 系統讓你追蹤玩家的「任務目標」並在 UI 中顯示進度。
典型用途：「找到圖書館的怪聲來源」、「集齊三張古地圖」、「在午夜前回到宿舍」。

---

## 快速範例

在 `content/quests.yaml`：

```yaml
quests:
  - id: find_ghost_book
    title: "尋找會消失的書"
    description: "圖書館特藏書庫深處傳來歌聲。也許那本書能解釋一切。"
    giver: qingyi
    objectives:
      - {id: visit_stacks, text: "進入特藏書庫"}
      - {id: find_book,    text: "找到那本古書"}
      - {id: read_book,    text: "讀完它", hidden: true}
      - {id: take_notes,   text: "抄下筆記", optional: true}
    rewards_text: "解鎖：青衣對你的信任 + 5"
```

在場景 YAML 啟動它：

```yaml
on_end:
  - {kind: start_quest, target: find_ghost_book}
```

完成一個目標：

```yaml
  - {kind: complete_objective, target: find_ghost_book, stat: visit_stacks}
```

---

## Quest YAML Schema

| 欄位           | 型別                | 必填 | 預設值       | 說明                                       |
|----------------|---------------------|------|--------------|--------------------------------------------|
| `id`           | string              | 是   |              | 唯一識別碼，在 effect / condition 裡引用   |
| `title`        | string              | 是   |              | 顯示在 UI 的任務名稱                       |
| `description`  | string              | 否   | `""`         | 長描述，顯示在 detail panel                |
| `giver`        | string \| null      | 否   | `null`       | NPC id 或地點 id，只用於敘述               |
| `objectives`   | list[Objective]     | 否   | `[]`         | 目標列表                                   |
| `rewards_text` | string              | 否   | `""`         | 完成後顯示的獎勵文字                       |
| `hidden`       | bool                | 否   | `false`      | `true` = 未啟動時不出現在 UI               |
| `status`       | QuestStatus         | 否   | `"inactive"` | `inactive / active / completed / failed`   |

### Objective Schema

| 欄位        | 型別   | 必填 | 預設值  | 說明                                                                  |
|-------------|--------|------|---------|-----------------------------------------------------------------------|
| `id`        | string | 是   |         | 在 `complete_objective` 的 `stat` 欄位裡引用                         |
| `text`      | string | 是   |         | 顯示給玩家的目標文字                                                  |
| `optional`  | bool   | 否   | `false` | `true` = 不強制完成；不阻擋 auto-complete                            |
| `completed` | bool   | 否   | `false` | 引擎管理；內容作者通常不需要手填                                      |
| `hidden`    | bool   | 否   | `false` | `true` = 未完成前不顯示給玩家（避免劇透子目標）                      |

---

## Effects

| kind                  | 說明                                                               |
|-----------------------|--------------------------------------------------------------------|
| `start_quest`         | 把 quest 從 `inactive` → `active`                                 |
| `complete_objective`  | 標記一個目標完成；`stat` = objective id                           |
| `complete_quest`      | 直接把 quest 設為 `completed`                                     |
| `fail_quest`          | 把 quest 設為 `failed`                                            |

完整語法見 [effects-reference.md](effects-reference.md)。

### Auto-complete 規則

`complete_objective` 套用後，引擎會檢查該 quest 的所有 **non-optional** objective
是否全部完成。若是，quest 自動變為 `completed`（不需要再寫 `complete_quest`）。

---

## Conditions

| kind                   | 說明                                           |
|------------------------|------------------------------------------------|
| `quest_active`         | quest 目前是 `active`                         |
| `quest_completed`      | quest 已 `completed`                          |
| `objective_completed`  | 某個 objective 已完成；`stat` = objective id  |

完整語法見 [conditions-reference.md](conditions-reference.md)。

---

## 狀態流程

```
inactive ──start_quest──> active ──complete_quest / auto-complete──> completed
                 │
                 └──fail_quest──> failed
```

- `complete_quest` 可以在任何狀態呼叫（`completed` 除外）。
- `fail_quest` 對已 `completed` 的 quest 無效。

---

## UI

玩家可從選單 **「任務記錄」** 進入 `QuestLogScene`（Overlay）。

左欄：進行中 / 已完成 / 失敗的任務列表（點選切換）。
右欄：選中任務的詳細資訊 — 標題、狀態、描述、目標清單、獎勵文字。

- `hidden: true` 的目標在未完成前不出現在右欄。
- `hidden: true` 的 quest 在 `inactive` 時不出現在左欄。

---

## 設計提示

1. **用 flag 做守衛而不是 quest_active**：quest status 不是永久的，
   `quest_active` 在完成後就變 false。若需要「曾啟動過」，搭配 `set_flag`。

2. **選擇性目標用 optional: true**：這樣它們不阻擋 auto-complete，
   但仍然顯示在 UI，讓玩家知道有額外內容可以完成。

3. **hidden objective 適合 spoiler-y 子目標**：例如「讀完它」這種
   只有進入書庫後才合理出現的目標，設 `hidden: true` 就不會提前透露。
