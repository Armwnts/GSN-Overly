"""Import des personnages de la vitrine du joueur (profil en jeu) via Enka.Network.

Le joueur entre son UID ; Enka renvoie les personnages affichés dans sa vitrine (jusqu'à 12 si
« Afficher les détails des personnages » est activé dans le jeu) : niveau, aptitudes,
constellations, arme, artéfacts et statistiques.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from pathlib import Path

import requests

ENKA = os.environ.get("GIO_ENKA_BASE", "https://enka.network")
STORE_URLS = [os.environ.get("GIO_ENKA_STORE", "https://raw.githubusercontent.com/EnkaNetwork/API-docs/master/store")
              + p for p in ("/gi/{f}", "/{f}")]
HEADERS = {"User-Agent": "GenshinOverlay/6.0 (outil personnel ; import de vitrine)"}
STORE_MAX_AGE = 7 * 24 * 3600

ELEM_SUFFIX = {"Wind": "anemo", "Rock": "geo", "Electric": "electro", "Grass": "dendro", "Water": "hydro",
               "Fire": "pyro", "Ice": "cryo"}
SLOTS = {"EQUIP_SHOES": "sablier", "EQUIP_RING": "coupe", "EQUIP_DRESS": "couronne"}
PROPS_FR = {"FIGHT_PROP_HP": "PV", "FIGHT_PROP_HP_PERCENT": "PV %", "FIGHT_PROP_ATTACK": "ATQ",
            "FIGHT_PROP_ATTACK_PERCENT": "ATQ %", "FIGHT_PROP_DEFENSE": "DÉF", "FIGHT_PROP_DEFENSE_PERCENT": "DÉF %",
            "FIGHT_PROP_CRITICAL": "Taux CRIT", "FIGHT_PROP_CRITICAL_HURT": "DGT CRIT",
            "FIGHT_PROP_CHARGE_EFFICIENCY": "Recharge", "FIGHT_PROP_ELEMENT_MASTERY": "Maîtrise élém.",
            "FIGHT_PROP_HEAL_ADD": "Bonus de soins", "FIGHT_PROP_PHYSICAL_ADD_HURT": "DGT physiques",
            "FIGHT_PROP_FIRE_ADD_HURT": "DGT Pyro", "FIGHT_PROP_ELEC_ADD_HURT": "DGT Électro",
            "FIGHT_PROP_WATER_ADD_HURT": "DGT Hydro", "FIGHT_PROP_GRASS_ADD_HURT": "DGT Dendro",
            "FIGHT_PROP_WIND_ADD_HURT": "DGT Anémo", "FIGHT_PROP_ROCK_ADD_HURT": "DGT Géo",
            "FIGHT_PROP_ICE_ADD_HURT": "DGT Cryo"}
ELEM_BONUS = {"40": "Pyro", "41": "Électro", "42": "Hydro", "43": "Dendro", "44": "Anémo", "45": "Géo", "46": "Cryo"}


class EnkaError(RuntimeError):
    pass


def _get(d: dict, *keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


class EnkaClient:
    def __init__(self, folder: Path):
        self.dir = folder
        self.dir.mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self._loc: dict | None = None
        self._chars: dict | None = None

    # ------------------------------------------------------------------ fichiers de référence
    def _store(self, fname: str, keep=None) -> dict:
        f = self.dir / fname
        if f.exists() and time.time() - f.stat().st_mtime < STORE_MAX_AGE:
            return json.loads(f.read_text(encoding="utf-8"))
        for tpl in STORE_URLS:
            try:
                r = self.s.get(tpl.format(f=fname), timeout=60)
                if r.ok:
                    d = r.json()
                    if keep:
                        d = keep(d)
                    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
                    return d
            except (requests.RequestException, ValueError):
                continue
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return {}

    def loc(self) -> dict:
        if self._loc is None:   # on ne garde que le français (le fichier complet est gros)
            self._loc = self._store("loc.json", keep=lambda d: d.get("fr") or d.get("FR") or d.get("en") or {})
        return self._loc

    def characters_store(self) -> dict:
        if self._chars is None:
            self._chars = self._store("characters.json")
        return self._chars

    # ------------------------------------------------------------------ vitrine
    def fetch(self, uid: str) -> dict:
        uid = re.sub(r"\D", "", uid or "")
        if not 9 <= len(uid) <= 10:
            raise EnkaError("UID invalide : il fait 9 ou 10 chiffres (en bas à droite de l'écran du jeu).")
        try:
            r = self.s.get(f"{ENKA}/api/uid/{uid}/", timeout=30)
        except requests.RequestException as e:
            raise EnkaError("Enka.Network ne répond pas (Internet ?).") from e
        msgs = {400: "UID invalide.", 404: "Aucun joueur avec cet UID.",
                424: "Maintenance du jeu en cours : réessaie plus tard.",
                429: "Trop de demandes : réessaie dans une minute.",
                500: "Enka.Network a un problème : réessaie plus tard.",
                503: "Enka.Network a un problème : réessaie plus tard."}
        if r.status_code != 200:
            raise EnkaError(msgs.get(r.status_code, f"Erreur Enka.Network ({r.status_code})."))
        try:
            return r.json()
        except ValueError as e:
            raise EnkaError("Réponse illisible d'Enka.Network.") from e

    def import_showcase(self, uid: str, gamedata) -> tuple[dict, list[dict]]:
        """Renvoie (infos joueur, liste de persos convertis pour « Mes persos »)."""
        data = self.fetch(uid)
        player = data.get("playerInfo") or {}
        avatars = data.get("avatarInfoList") or []
        info = {"nickname": player.get("nickname", ""), "level": player.get("level"),
                "world_level": player.get("worldLevel"), "count": len(avatars),
                "showcase": len(player.get("showAvatarInfoList") or [])}
        if not avatars:
            return info, []
        loc = self.loc()
        store = self.characters_store()
        known = {c["id"] for c in gamedata.characters()}
        out = []
        for a in avatars:
            out.append(convert_avatar(a, loc, store, gamedata, known))
        return info, [o for o in out if o]


def _prop(a: dict, key: str) -> int:
    pm = a.get("propMap") or {}
    v = pm.get(key) or {}
    try:
        return int(v.get("val") or v.get("ival") or 0)
    except (TypeError, ValueError):
        return 0


def _pct(v) -> str:
    return f"{float(v) * 100:.1f} %".replace(".", ",")


def convert_avatar(a: dict, loc: dict, store: dict, gamedata, known: set[str]) -> dict | None:
    aid = str(_get(a, "avatarId", "avatarID", default=""))
    cid = aid
    if aid in ("10000005", "10000007"):   # Voyageur : élément selon le jeu de compétences
        depot = str(a.get("skillDepotId", ""))
        elem = (store.get(f"{aid}-{depot}") or {}).get("Element", "")
        suf = ELEM_SUFFIX.get(elem)
        cid = f"10000005-{suf}" if suf else next((k for k in sorted(known) if k.startswith("10000005-")), aid)
    if cid not in known:
        return None
    phase = max(0, min(6, _prop(a, "1002")))
    level = _prop(a, "4001")
    cons = len(a.get("talentIdList") or [])
    # Aptitudes : niveaux de base (sans bonus de constellation), dans l'ordre des aptitudes du perso
    skills = {str(k): int(v) for k, v in (a.get("skillLevelMap") or {}).items()}
    talents = [1, 1, 1]
    try:
        from .gamedata import active_talents
        for i, t in enumerate(active_talents(gamedata.detail(cid))[:3]):
            talents[i] = max(1, min(10, skills.get(str(t.get("skillId")), 1)))
    except Exception:  # noqa: BLE001 — sans détail du perso : aptitudes laissées à 1
        pass

    weapon, sets, mains = "", Counter(), {}
    for e in a.get("equipList") or []:
        flat = e.get("flat") or {}
        name = loc.get(str(_get(flat, "nameTextMapHash", "nameTextHashMap", default="")), "")
        if flat.get("itemType") == "ITEM_WEAPON" or "weapon" in e:
            w = e.get("weapon") or {}
            refine = (max((w.get("affixMap") or {}).values(), default=0) or 0) + 1
            stars = flat.get("rankLevel", "")
            weapon = f"{name or 'Arme'} R{refine}, niv. {w.get('level', '?')}" + (f" ({stars}★)" if stars else "")
        else:
            s = loc.get(str(_get(flat, "setNameTextMapHash", "setNameTextHashMap", default="")), "")
            if s:
                sets[s] += 1
            slot = SLOTS.get(flat.get("equipType", ""))
            ms = flat.get("reliquaryMainstat") or {}
            if slot and ms:
                mains[slot] = PROPS_FR.get(ms.get("mainPropId", ""), ms.get("mainPropId", "?"))
    parts = [f"{n} pièces {s}" for s, n in sets.most_common() if n >= 2]
    art = " + ".join(parts) or ("sets mélangés" if sets else "")
    if mains:
        art += (" ; " if art else "") + ", ".join(f"{k} {v}" for k, v in mains.items())

    fp = {str(k): v for k, v in (a.get("fightPropMap") or {}).items()}
    stats = []
    for key, lab in (("2000", "PV"), ("2001", "ATQ"), ("2002", "DÉF")):
        if fp.get(key):
            stats.append(f"{lab} {int(round(float(fp[key])))}")
    if fp.get("28"):
        stats.append(f"ME {int(round(float(fp['28'])))}")
    for key, lab in (("20", "Taux CRIT"), ("22", "DGT CRIT"), ("23", "Recharge")):
        if fp.get(key) is not None:
            stats.append(f"{lab} {_pct(fp[key])}")
    bonus = max(((v, ELEM_BONUS[k]) for k, v in fp.items() if k in ELEM_BONUS and float(v) > 0), default=None)
    if bonus:
        stats.append(f"Bonus DGT {bonus[1]} {_pct(bonus[0])}")
    return {"id": cid, "level": level, "phase": phase, "talents": talents, "cons": cons,
            "weapon": weapon, "artifacts": art, "stats": ", ".join(stats)}


def apply_to_roster(roster, imported: list[dict]) -> int:
    """Ajoute / met à jour les persos dans « Mes persos » (les objectifs sont conservés)."""
    for c in imported:
        if not roster.is_owned(c["id"]):
            roster.set_owned(c["id"], True)
        st = roster.get(c["id"])
        tt = [max(t, g) for t, g in zip(c["talents"], st["talent_targets"])]
        roster.owned[c["id"]].update(phase=c["phase"], target_phase=max(c["phase"], st["target_phase"]),
                                     talents=c["talents"], talent_targets=tt, cons=c["cons"],
                                     weapon=c["weapon"], artifacts=c["artifacts"], stats=c["stats"],
                                     imported=time.time())
    roster.save()
    return len(imported)
