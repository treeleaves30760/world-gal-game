# Pack Format

一個 *pack* 就是一個目錄；放進引擎找得到的地方，
`world-gal-game --pack <name>` 就能玩。

## 完整目錄結構

```
my_game/
├── pyproject.toml          # 選填，把 pack 包成 pip-installable
├── README.md
├── main.py                 # 選填，當 standalone 跑時的入口
├── content/                # 必要 — 引擎只認這個資料夾
│   ├── meta.yaml           # 必要 — 沒有就不算 pack
│   ├── locations.yaml      # 選填
│   ├── characters.yaml     # 選填
│   ├── items.yaml          # 選填
│   ├── resources.yaml      # 選填（或寫在 meta.yaml）
│   ├── achievements.yaml   # 選填
│   └── scenes/             # 選填，多檔散裝
│       ├── 00_*.yaml
│       └── ...
└── assets/                 # 選填 — 不存在時引擎用 placeholder
    ├── backgrounds/
    ├── characters/
    ├── cgs/
    ├── ui/
    ├── fonts/
    └── bgm/
```

**所有 YAML 內的資產路徑寫 `assets/...`（pack-相對）**，例如
`assets/backgrounds/library.png`。引擎在載入時會自動把它對應到這個
pack 的根目錄。

## meta.yaml（必要）

最小可行：

```yaml
title: "我的遊戲"
subtitle: "副標題"
start_location: starting_room    # 開新遊戲時把玩家放在這個地點
intro_scene: prologue            # 開新遊戲後第一個自動播的場景
```

完整可選欄位：

```yaml
title: "我的遊戲"
subtitle: "副標題"

# 文字顯示速度 (chars/sec)。0 = 瞬間。
text_speed: 60

# 內嵌字型；CJK 一律建議內嵌避免使用者系統沒有字型。
bundled_font: assets/fonts/MyFont.ttf

# 標題畫面背景圖。
title_bg: assets/backgrounds/title.png

# 起始地點 / 場景。
start_location: starting_room
intro_scene: prologue

# 玩家預設名字、第一人稱代詞（標題畫面可以改）。
player:
  name: "玩家"
  pronouns: "他"

# 起始持有物品（key = item id, value = count）。
starting_inventory:
  jasmine_tea: 1
  espresso: 1

# 起始資源值（key = resource id, value = amount）。
# 也可以直接在 resources 定義 starting；兩個都會被讀。
starting_resources:
  money: 500
  energy: 100

# Theme 覆寫（不寫的欄位用引擎預設）。
theme:
  accent:      [216, 80, 143]    # RGB or RGBA
  accent_alt:  [107, 107, 255]
  accent_warm: [240, 198, 116]
  bg_deep:     [13, 10, 20]
  pad_m: 14
  radius_m: 10

# 在地化覆寫 — 改 affection 等級、時段、UI 文字。
locale:
  affection_levels:
    - {min: 0,   label: "Stranger"}
    - {min: 25,  label: "Friend"}
    - {min: 100, label: "Lover"}
  time_of_day:
    morning: "Morning"
    midnight: "Witching Hour"
  ui:
    new_game: "New Game"
    map: "Map"

# 資源宣告 — 也可以寫在 content/resources.yaml。
resources:
  - id: money
    name: "錢包"
    symbol: "$"
    starting: 500
    min: 0
```

詳細欄位：
- [theme-and-locale.md](theme-and-locale.md) — theme / locale 區塊
- [resources.md](resources.md) — resources 區塊

## 引擎在哪裡找 pack？

```
1. <engine-repo>/games/<name>/           — 隨引擎散布的內建 pack
2. <engine-repo>/../<name>/              — 兄弟資料夾（建議）
3. ~/.world-gal-game/packs/<name>/        — 使用者本機 pack 緩存
4. --pack <abs/rel-path>                 — 直接指定
```

`<name>` 會自動嘗試 snake_case、kebab-case、Title-Case 變體。
所以 `--pack tsing_hua_strange_tales` 也會找到 `Tsing-Hua-Strange-Tales/`。

## 讓 pack 變成 pip-installable

如果你想把 pack 也包成可 `uv add` 的東西（給玩家或 CI 用）：

`pyproject.toml`：

```toml
[project]
name = "my-game"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["world-gal-game>=0.1.0"]

[project.scripts]
my-game = "my_game.entry:main"

[tool.uv.sources]
# 本地開發指向 sibling 引擎源碼；未來上 PyPI 後可拿掉這段。
world-gal-game = { path = "../World-Gal-Game", editable = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["my_game"]
include = ["content/**/*", "assets/**/*"]

[tool.hatch.build.targets.wheel.force-include]
"content" = "my_game/content"
"assets" = "my_game/assets"
```

`my_game/__init__.py` + `my_game/entry.py`（薄殼）：

```python
# my_game/entry.py
from pathlib import Path
import sys


def _pack_root() -> Path:
    here = Path(__file__).resolve().parent
    if (here / "content" / "meta.yaml").exists():
        return here          # wheel install
    return here.parent       # source checkout


def main(argv: list[str] | None = None) -> int:
    pack_root = _pack_root()
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    if "--pack" not in forwarded:
        forwarded = ["--pack", str(pack_root)] + forwarded
    from world_gal_game.cli import main as engine_main
    return engine_main(forwarded)


if __name__ == "__main__":
    sys.exit(main())
```

之後使用者只要：

```bash
uv pip install my-game
my-game             # console script 直接啟動，引擎自動載這個 pack
```

範例見 sibling 的 `Tsinghua-Strange-Tales/`。
