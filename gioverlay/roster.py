"""« Mes persos » : personnages débloqués, progression, objectifs, équipement et inventaire de matériaux."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT = {"phase": 0, "target_phase": 6, "talents": [1, 1, 1], "talent_targets": [8, 8, 8],
           "cons": 0, "weapon": "", "artifacts": "", "stats": "", "notes": ""}


class Roster:
    def __init__(self, path: Path):
        self.path = path
        self.owned: dict[str, dict] = {}
        self.inventory: dict[str, int] = {}
        self.used_codes: set[str] = set()
        if path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                self.owned = d.get("owned", {})
                self.inventory = {k: int(v) for k, v in d.get("inventory", {}).items()}
                self.used_codes = set(d.get("used_codes", []))
            except (ValueError, OSError):
                pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"owned": self.owned, "inventory": self.inventory,
                                   "used_codes": sorted(self.used_codes)}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)

    def is_owned(self, cid: str) -> bool:
        return cid in self.owned

    def get(self, cid: str) -> dict:
        d = dict(DEFAULT)
        d.update(self.owned.get(cid, {}))
        return d

    def set_owned(self, cid: str, on: bool):
        if on and cid not in self.owned:
            self.owned[cid] = dict(DEFAULT)
        elif not on:
            self.owned.pop(cid, None)
        self.save()

    def update(self, cid: str, **kw):
        if cid in self.owned:
            self.owned[cid].update(kw)
            self.save()
