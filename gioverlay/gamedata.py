"""Données du jeu (personnages, matériaux d'élévation et d'aptitudes) depuis Project Yatta
(gi.yatta.moe), en français, gardées en cache sur le disque.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

API = os.environ.get("GIO_YATTA_BASE", "https://gi.yatta.moe") + "/api/v2/fr"
ICON_URLS = [os.environ.get("GIO_YATTA_BASE", "https://gi.yatta.moe") + "/assets/UI/{icon}.png",
             "https://enka.network/ui/{icon}.png"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) GenshinOverlay/5.0 (outil personnel)"}
LIST_MAX_AGE = 3 * 24 * 3600
DETAIL_MAX_AGE = 14 * 24 * 3600

ELEMENTS = {"Fire": "Pyro", "Water": "Hydro", "Wind": "Anémo", "Electric": "Électro", "Ice": "Cryo",
            "Rock": "Géo", "Grass": "Dendro"}
ELEMENT_COLORS = {"Pyro": "#ef7a35", "Hydro": "#4cc2f1", "Anémo": "#74c2a8", "Électro": "#b08fc2",
                  "Cryo": "#9fd6e3", "Géo": "#f0b232", "Dendro": "#a5c83b"}
WEAPONS = {"WEAPON_SWORD_ONE_HAND": "Épée", "WEAPON_CLAYMORE": "Épée à deux mains", "WEAPON_POLE": "Arme d'hast",
           "WEAPON_BOW": "Arc", "WEAPON_CATALYST": "Catalyseur"}
# Phase d'élévation (0 à 6) -> niveau maximum
PHASE_LABELS = ["Niv. 20 (pas élevé)", "Niv. 40 (élévation 1)", "Niv. 50 (élévation 2)", "Niv. 60 (élévation 3)",
                "Niv. 70 (élévation 4)", "Niv. 80 (élévation 5)", "Niv. 90 (élévation 6)"]
MORA_ID = "202"


class GameDataError(RuntimeError):
    pass


class GameData:
    def __init__(self, folder: Path):
        self.dir = folder
        (self.dir / "icons").mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._details: dict[str, dict] = {}

    def _get_json(self, url: str) -> dict:
        try:
            r = self.session.get(url, timeout=25)
            r.raise_for_status()
            d = r.json()
        except (requests.RequestException, ValueError) as e:
            raise GameDataError(f"Impossible de joindre la base de données du jeu ({type(e).__name__}).") from e
        if not isinstance(d, dict) or "data" not in d:
            raise GameDataError("Réponse inattendue de la base de données du jeu.")
        return d["data"]

    def _cached(self, name: str, url: str, max_age: float, refresh=False) -> dict:
        f = self.dir / name
        if f.exists() and not refresh:
            d = json.loads(f.read_text(encoding="utf-8"))
            if time.time() - d.get("_fetched", 0) < max_age:
                return d
        try:
            d = self._get_json(url)
        except GameDataError:
            if f.exists():  # hors ligne : dernière version connue
                return json.loads(f.read_text(encoding="utf-8"))
            raise
        d["_fetched"] = time.time()
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return d

    # ------------------------------------------------------------------ personnages
    def characters(self, refresh=False) -> list[dict]:
        d = self._cached("avatars.json", f"{API}/avatar", LIST_MAX_AGE, refresh)
        out, seen_traveler = [], set()
        for cid, c in (d.get("items") or {}).items():
            cid = str(c.get("id", cid))
            elem = ELEMENTS.get(c.get("element", ""), c.get("element", ""))
            name = c.get("name", cid)
            if cid.startswith("10000007-"):   # Voyageuse : mêmes matériaux que le Voyageur
                continue
            if cid.startswith("10000005-"):
                name = f"Voyageur ({elem})"
                seen_traveler.add(elem)
            out.append({"id": cid, "name": name, "rank": c.get("rank", 4), "element": elem,
                        "weapon": WEAPONS.get(c.get("weaponType", ""), ""), "region": c.get("region", ""),
                        "icon": c.get("icon", ""), "release": c.get("release") or 0})
        return sorted(out, key=lambda c: c["name"].lower())

    def detail(self, cid: str, refresh=False) -> dict:
        if cid in self._details and not refresh:
            return self._details[cid]
        safe = re.sub(r"[^\w-]", "_", cid)
        d = self._cached(f"avatar_{safe}.json", f"{API}/avatar/{cid}", DETAIL_MAX_AGE, refresh)
        self._details[cid] = d
        return d

    def icon(self, icon: str) -> Path | None:
        if not icon:
            return None
        p = self.dir / "icons" / (re.sub(r"[^\w-]", "_", icon) + ".png")
        if p.exists() and p.stat().st_size > 0:
            return p
        for tpl in ICON_URLS:
            try:
                r = self.session.get(tpl.format(icon=icon), timeout=20)
                if r.ok and len(r.content) > 64 and (r.headers.get("content-type", "").startswith("image")
                                                      or r.content[:4] == b"\x89PNG"):
                    p.write_bytes(r.content)
                    return p
            except requests.RequestException:
                continue
        return None


# ---------------------------------------------------------------------- calculs
def active_talents(detail: dict) -> list[dict]:
    """Les 3 aptitudes qui s'améliorent (attaque normale, compétence, déchaînement)."""
    out = []
    for _k, t in sorted((detail.get("talent") or {}).items(), key=lambda kv: int(re.sub(r"\D", "", kv[0]) or 0)):
        pr = t.get("promote") or {}
        if (pr.get("2") or {}).get("costItems"):
            out.append(t)
    return out[:3]


def needs(detail: dict, phase: int, target_phase: int, talents: list[int], talent_targets: list[int]) -> dict[str, int]:
    """Matériaux (id -> quantité, Mora compris sous l'id 202) pour aller de l'état actuel à l'objectif."""
    tot: dict[str, int] = {}

    def add(items, mora):
        for k, v in (items or {}).items():
            tot[str(k)] = tot.get(str(k), 0) + int(v)
        if mora:
            tot[MORA_ID] = tot.get(MORA_ID, 0) + int(mora)

    promote = {p.get("promoteLevel"): p for p in ((detail.get("upgrade") or {}).get("promote") or [])}
    for p in range(max(0, phase) + 1, min(6, target_phase) + 1):
        e = promote.get(p) or {}
        add(e.get("costItems"), e.get("coinCost"))
    for t, cur, tgt in zip(active_talents(detail), talents, talent_targets):
        pr = t.get("promote") or {}
        for lv in range(max(1, cur) + 1, min(10, tgt) + 1):
            e = pr.get(str(lv)) or {}
            add(e.get("costItems"), e.get("coinCost"))
    return tot


def item_info(detail: dict, iid: str) -> dict:
    it = (detail.get("items") or {}).get(str(iid)) or {}
    return {"id": str(iid), "name": it.get("name", f"Objet {iid}"), "rank": it.get("rank", 1),
            "icon": it.get("icon", f"UI_ItemIcon_{iid}")}
