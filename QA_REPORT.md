# QA Report — 2026-05-18

## 測試覆蓋

- 引擎單元測試：198 通過 / 0 失敗
- 整合測試（新增）：19 個新測試（17 通過 / 0 失敗 / 2 xfail 記錄已知 bug）
- Tsing-Hua walkthrough ending：4 條 ending 全通（qingyi / yuening / xiangxiang / alone-check）
- 互動：NPC 對話 overlay、shop 買賣、gift 好感增減 全部驗證通過
- 地圖：travel_cost、time-blocked exit、visited 標記 全部驗證通過
- Quest：start / complete_objective / auto-complete / quest log describe() 全部驗證通過
- 已知 bug regression：typewriter force_reveal、speaker interpolation、top bar layout、exit travel_cost、disabled exit toast 全部仍綠

## 發現的問題

---

### Bug #SL-001 — 存檔無法正確 round-trip 載回（P0）

**問題標題**：SaveManager 序列化 Python `set` 為 repr 字串，GameState 無法解析

**重現步驟**：

```python
# pytest tests/test_qa_walkthrough.py::test_save_load_round_trip
from world_gal_game.core.save_manager import SaveManager
from world_gal_game.core.game_state import GameState
import json, tempfile, pathlib

# Setup: driver.new_game() + skip_dialogue 後取 state
# ...

sm = SaveManager(tmp_path)
sm.save("slot", state.model_dump(), label="test")

raw = sm.load("slot")
for k in ("_saved_at", "_label", "_summary", "_schema_version", "_thumbnail_path"):
    raw.pop(k, None)

# 這行 raise pydantic_core.ValidationError: 12 validation errors
GameState(**raw)
```

**根本原因**：`SaveManager.save()` 呼叫 `json.dump(..., default=str)`。Python 的 `json` 模組遇到無法序列化的型別時會呼叫 `default=str`，而 `set` 不是 JSON 原生型別，所以 `set()` 被轉換為字串 `"set()"`，`{'player_dorm'}` 被轉換為字串 `"{'player_dorm'}"`。  
Pydantic 在 `GameState(**raw)` 時無法把字串 `"{'player_dorm'}"` 解析為 `set[str]`，所以 12 個 set 型欄位全部 validation 失敗：

- `affection.characters.*.unlocked`（9 個字元）
- `map.visited`
- `story.played`
- `achievements.seen`

**預期行為**：存檔 → 移動 → 載入 → 回到存檔時的狀態（位置、資源、flags）

**實際行為**：`GameState(**loaded_data)` 拋出 `pydantic_core.ValidationError: 12 validation errors`

**修復方向**：在 `SaveManager.save()` 改用自訂 JSON encoder 把 `set` 轉為 `list`（Pydantic 可以從 `list` 重建 `set`），或在 `model_dump()` 時傳入 `mode='json'`（Pydantic v2 的 `model.model_dump(mode='json')` 會正確序列化所有型別）。

**嚴重程度**：P0 — 存檔系統完全無法 round-trip，玩家存完檔後無法讀取回正確狀態。

**截圖**：N/A（崩潰發生在 Pydantic 驗證層，不產生畫面）

**重現 pytest 函式**：`tests/test_qa_walkthrough.py::test_save_load_round_trip`（標記 `xfail strict=True`）

---

### Bug #QL-001 — QuestLogScene 開啟後每 frame 都 crash（P1）

**問題標題**：`QuestLog.update()` 使用不存在的 `inp.click_pos` 屬性

**重現步驟**：

```python
# pytest tests/test_qa_quests.py::test_quest_log_overlay_renders
from world_gal_game.dev.driver import GameDriver
d = GameDriver(pack="tsing_hua_strange_tales")
d.new_game()
d.skip_dialogue()
d.app._open_quest_log()
d.app.manager.commit_pending()
d.advance_frames(1)  # crash here
```

錯誤訊息：

```
AttributeError: 'InputState' object has no attribute 'click_pos'
  File "world_gal_game/ui/widgets/quest_log.py", line 226, in update
    if inp.click_pos is not None:
```

**根本原因**：`world_gal_game/ui/widgets/quest_log.py` 第 226 行：

```python
if inp.click_pos is not None:
```

`InputState`（`world_gal_game/ui/input.py`）沒有 `click_pos` 屬性。只有 `mouse_pos`（始終有值）和 `mouse_clicked`（布林）。`click_pos` 是一個從未被加到 `InputState` dataclass 上的概念欄位。

**預期行為**：QuestLogScene 開啟、顯示任務列表、可以用滑鼠點擊選擇任務

**實際行為**：開啟後第一個 `advance_frames()` 就 crash，玩家完全無法使用任務記錄

**修復方向**：將 `quest_log.py:226` 的 `inp.click_pos is not None` 改為 `inp.mouse_clicked`，並將 `inp.click_pos` 改為 `inp.mouse_pos if inp.mouse_clicked else None`。

**嚴重程度**：P1 — 整個任務記錄系統對玩家完全不可用（每次開啟都 crash）

**截圖**：N/A（崩潰在引擎層，不產生畫面輸出）

**重現 pytest 函式**：`tests/test_qa_quests.py::test_quest_log_overlay_renders`（標記 `xfail strict=True`）

---

## 已知 regression 通過項目

以下為上一輪修過的項目，確認仍綠：

| 項目 | 測試來源 | 結果 |
|---|---|---|
| typewriter force_reveal 單調性 | `tests/test_game_driver.py::test_driver_typewriter_skip_via_space` | PASS |
| speaker interpolation | `tests/test_text_interpolation.py::test_speaker_field_interpolated_via_engine` | PASS |
| top bar layout（探索場景渲染） | `tests/test_game_driver.py::test_driver_find_widget_by_label` | PASS |
| exit travel_cost 時間推進 | `tests/test_qa_map.py::test_cross_region_move_advances_time` | PASS |
| disabled exit 顯示 toast 不 crash | `tests/test_qa_map.py::test_blocked_exit_shows_toast_not_crash` | PASS |

## 通過項目

- 引擎所有 198 個單元測試：全綠
- TitleScene → new_game → DialogueScene (prologue_arrival)
- skip_dialogue → ExplorationScene，intro_done flag 正確
- menu overlay 開啟 7 個 overlay（map, affection, event_log, achievements, inventory, save, load）並以 Esc 關閉
- qingyi / yuening / xiangxiang 三條 ending 正確設定結束 flag 與 quest completion
- alone-check：零好感起始狀態確認無 ending flag
- NPC 卡牌點擊 → NPCActionScene 含「送禮」「看貨」按鈕
- 商店購買：money 扣除正確，inventory 增加正確
- 商店賣出：money 增加正確，inventory 減少正確
- 送禮好感：喜好物品 +affection，厭惡物品 -affection
- 本地移動不推進時間
- 跨 region 移動（travel_cost=2）推進 2 時段
- 時間限制出口白天點擊不 crash 不移動
- visited 集合正確更新，MapScene 反映 visited=true/false
- quest start → quests_active，QuestLogScene.describe() 含 id
- quest complete_objective 自動 complete
