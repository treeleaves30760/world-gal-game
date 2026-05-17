"""Save / load to JSON.

The whole GameState is pydantic, so we just dump model_dump() to disk.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SaveManager:
    def __init__(self, save_dir: Path):
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)

    def list_saves(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in sorted(self.save_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                out.append({
                    "slot": p.stem,
                    "path": str(p),
                    "saved_at": data.get("_saved_at"),
                    "summary": data.get("_summary", ""),
                    "label": data.get("_label", p.stem),
                })
            except Exception:
                continue
        return out

    def save(self, slot: str, state_dict: dict[str, Any], *,
             label: str = "", summary: str = "") -> Path:
        payload = dict(state_dict)
        # Strip internal bridges that should not be persisted.
        if "meta" in payload and isinstance(payload["meta"], dict):
            payload["meta"] = {k: v for k, v in payload["meta"].items()
                               if not k.startswith("__")}
        payload["_saved_at"] = datetime.now(timezone.utc).isoformat()
        payload["_label"] = label or slot
        payload["_summary"] = summary
        path = self.save_dir / f"{slot}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        return path

    def load(self, slot: str) -> dict[str, Any]:
        path = self.save_dir / f"{slot}.json"
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def delete(self, slot: str) -> bool:
        path = self.save_dir / f"{slot}.json"
        if path.exists():
            path.unlink()
            return True
        return False
