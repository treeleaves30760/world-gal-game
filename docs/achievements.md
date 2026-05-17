# Achievements

成就是宣告式的：你寫一份 condition 列表，引擎在每次 effect 套用後
自動重新檢查、達標時觸發解鎖 + toast 通知。

## 宣告

`content/achievements.yaml`：

```yaml
achievements:
  - id: ach_first_step
    title: "新生入學"
    description: "踏出宿舍房間，正式開始校園生活。"
    icon: assets/ui/ach_first_step.png    # 選填，沒寫就用文字 chip
    requires:
      - {kind: flag, target: orientation_done}

  - id: ach_meet_three
    title: "三條岔路"
    description: "與三位女主角都見過面。"
    requires:
      - {kind: flag, target: met_qingyi}
      - {kind: flag, target: met_yuening}
      - {kind: flag, target: met_xiangxiang}

  - id: ach_qingyi_lover
    title: "舊書與晚風"
    description: "與林青衣的故事走到了真正的結局。"
    hidden: true                          # 解鎖前在成就頁顯示「？？？」
    requires:
      - {kind: flag, target: ending_qingyi}

  # 排除型：要滿足 requires 同時不滿足 forbids
  - id: ach_pacifist
    title: "和平主義"
    description: "整個故事沒打過任何一架。"
    requires:
      - {kind: scene_played, target: ending_any}
    forbids:
      - {kind: flag, target: started_fight}
```

完整欄位：

| 欄位 | 必要 | 說明 |
|---|---|---|
| `id` | ✓ | 唯一 id |
| `title` / `description` | ✓ | UI 顯示文字 |
| `icon` | | 圖示路徑；沒寫用文字 chip |
| `hidden` | | true 時解鎖前完全隱藏（顯示為「？？？」） |
| `requires` | | 全部成立才解鎖 |
| `forbids` | | 任何一個成立就不解鎖（會永遠擋住） |

## 引擎怎麼觸發

`GameState.apply_all()` — 任何一組 effects 被套用後 — 會把成就 tracker
跑一遍：每個沒解鎖的成就，檢查 `requires` 是否全真、`forbids` 是否全假。
新解鎖的會：

1. 寫進事件記錄（kind = "unlock"）
2. 推進 toast queue → 下一幀右上角浮出來
3. 出現在「成就」overlay

成就一旦解鎖就 sticky — 即使後來條件不再成立也不會撤銷。

## 在條件裡查詢成就

```yaml
requires:
  - {kind: achievement, target: ach_first_step}
```

可以拿來鎖後面的對白或場景，例如「達成成就 X 後才開放某個結局」。

## 從 headless 觸發

```json
{"op": "set_flag", "key": "ending_qingyi", "value": true}
{"op": "inspect"}
```

設 flag 也會觸發成就 re-evaluate。snapshot 裡的 `achievements.unlocked`
會列出已解鎖 id。

## UI

按 T 鍵或從「選單」打開「成就」overlay。

- 已解鎖：彩色 chip + title + 描述 + 解鎖時間戳
- 未解鎖：灰色 chip + title 仍可見（hidden=false 時）
- hidden 未解鎖：完全黑掉的 "?" chip + title 顯示為「？？？」

## 設計建議

- **不要用成就當條件鎖核心劇情**。`{kind: achievement, target: …}` 雖然可以這樣用，
  但成就的本質是「玩家行為的副產品」，當主軸驅動會混淆關係。要鎖場景就用 flag。
- **隱藏成就要不能被 spoiler**：title 解鎖前完全不出現，所以可以放結局或 routes
  的內容。題材敏感（如壞結局名稱）建議都 hidden。
- **數量設計**：每個女主角 2-3 個（meet / friendship climax / ending），
  整體目標 12-30 個對中等流程 (5-15 hours) 是舒服的密度。
