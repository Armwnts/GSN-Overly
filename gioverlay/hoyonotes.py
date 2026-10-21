"""Notes en temps réel (HoYoLAB « Battle Chronicle ») et connexion quotidienne.

Il faut les cookies HoYoLAB du joueur (ltuid_v2 et ltoken_v2), copiés depuis son navigateur.
Ils restent sur son ordinateur (config.json) et ne servent qu'à appeler HoYoLAB.
"""
from __future__ import annotations

import hashlib
import os
import random
import re
import string
import time

import requests

RECORD = os.environ.get("GIO_HOYO_RECORD", "https://sg-public-api.hoyolab.com/event/game_record/genshin/api")
SOL = os.environ.get("GIO_HOYO_SOL", "https://sg-hk4e-api.hoyolab.com/event/sol")
LEDGER = os.environ.get("GIO_HOYO_LEDGER", "https://sg-hk4e-api.hoyolab.com/event/ysledgeros/month_info")
ACT_ID = "e202102251931481"
DS_SALT = "6s25p5ox5y14umn1p61aqyyvbvvl3lrt"
RESIN_MINUTES = 8          # 1 résine toutes les 8 minutes
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                         "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
           "Origin": "https://act.hoyolab.com", "Referer": "https://act.hoyolab.com/",
           "x-rpc-app_version": "1.5.0", "x-rpc-client_type": "5", "x-rpc-language": "fr-fr"}
COOKIE_KEYS = ("ltuid_v2", "ltoken_v2", "ltmid_v2", "account_id_v2", "cookie_token_v2", "account_mid_v2")


class HoyoError(RuntimeError):
    pass


def parse_cookies(text: str) -> dict:
    """Accepte la ligne « Cookie: … » complète, ou des lignes « nom=valeur » / « nom valeur »."""
    out = {}
    for part in re.split(r"[;\n]+", text or ""):
        m = re.match(r"\s*(?:cookie:\s*)?([\w]+)\s*[=:\t ]\s*\"?([^\s\";]+)\"?", part, re.I)
        if m and m.group(1) in COOKIE_KEYS:
            out[m.group(1)] = m.group(2)
    return out


def server_for(uid: str) -> str:
    uid = re.sub(r"\D", "", uid or "")
    if uid.startswith("18") and len(uid) == 10:
        return "os_asia"
    return {"6": "os_usa", "7": "os_euro", "8": "os_asia", "9": "os_cht"}.get(uid[:1], "os_euro")


def _ds() -> str:
    t = int(time.time())
    r = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    h = hashlib.md5(f"salt={DS_SALT}&t={t}&r={r}".encode()).hexdigest()
    return f"{t},{r},{h}"


def _check(d: dict, what: str):
    rc = d.get("retcode")
    if rc in (0, None):
        return
    msg = d.get("message", "")
    if rc in (10102,):
        raise HoyoError("Tes notes ne sont pas publiques : sur HoYoLAB → ton profil → Battle Chronicle → "
                        "⚙ Paramètres → active « Notes en temps réel ».")
    if rc in (-100, 10001, -101, 10103):
        raise HoyoError("Cookies HoYoLAB refusés ou expirés : recopie-les dans les Paramètres.")
    if rc in (1034, 5003, 10035, 10041):
        raise HoyoError("HoYoLAB demande une vérification : ouvre HoYoLAB (Battle Chronicle) dans ton "
                        "navigateur une fois, puis réessaie.")
    if rc == 10104:
        raise HoyoError("Cet UID n'est pas lié à ce compte HoYoLAB.")
    raise HoyoError(f"HoYoLAB ({what}) : {msg or 'erreur'} [{rc}]")


class HoyoClient:
    def __init__(self, cookies: dict, uid: str):
        if not cookies.get("ltuid_v2") or not cookies.get("ltoken_v2"):
            raise HoyoError("Il manque les cookies ltuid_v2 et ltoken_v2 (voir les Paramètres).")
        if not re.sub(r"\D", "", uid or ""):
            raise HoyoError("Indique ton UID dans les Paramètres.")
        self.uid = re.sub(r"\D", "", uid)
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.s.cookies.update(cookies)

    def _req(self, method, url, what, **kw):
        try:
            r = self.s.request(method, url, timeout=20, **kw)
            d = r.json()
        except requests.RequestException as e:
            raise HoyoError("HoYoLAB ne répond pas (Internet ?).") from e
        except ValueError as e:
            raise HoyoError("Réponse illisible de HoYoLAB.") from e
        _check(d, what)
        return d.get("data") or {}

    def notes(self) -> dict:
        d = self._req("GET", f"{RECORD}/dailyNote", "notes",
                      params={"server": server_for(self.uid), "role_id": self.uid}, headers={"DS": _ds()})
        return parse_notes(d)

    def checkin(self) -> tuple[str, str]:
        """Récupère la récompense de connexion du jour. Renvoie (état, récompense)."""
        p = {"act_id": ACT_ID, "lang": "fr-fr"}
        info = self._req("GET", f"{SOL}/info", "connexion", params=p)
        home = self._req("GET", f"{SOL}/home", "connexion", params=p)
        awards = home.get("awards") or []
        n = int(info.get("total_sign_day") or 0)
        if info.get("is_sign"):
            prev = awards[n - 1] if 0 < n <= len(awards) else {}
            return "déjà", f"{prev.get('name', '')} ×{prev.get('cnt', '')}".strip(" ×")
        try:
            r = self.s.post(f"{SOL}/sign", params={"lang": "fr-fr"}, json={"act_id": ACT_ID}, timeout=20)
            d = r.json()
        except (requests.RequestException, ValueError) as e:
            raise HoyoError("HoYoLAB ne répond pas (Internet ?).") from e
        if d.get("retcode") == -5003:
            return "déjà", ""
        _check(d, "connexion")
        if (d.get("data") or {}).get("gt_result", {}).get("is_risk"):
            raise HoyoError("HoYoLAB demande une vérification : fais la connexion du jour à la main une fois.")
        aw = awards[n] if n < len(awards) else {}
        return "ok", f"{aw.get('name', '')} ×{aw.get('cnt', '')}".strip(" ×")


    # ------------------------------------------------------------------ compte (v12)
    def index(self) -> dict:
        """Exploration officielle par région + statistiques (coffres ouverts, oculus…)."""
        d = self._req("GET", f"{RECORD}/index", "exploration",
                      params={"server": server_for(self.uid), "role_id": self.uid, "avatar_list_type": 0},
                      headers={"DS": _ds()})
        return parse_index(d)

    def ledger(self, month: int | None = None) -> dict:
        """Journal du voyageur : primo-gemmes et Mora gagnés ce mois-ci (et le mois dernier)."""
        p = {"uid": self.uid, "region": server_for(self.uid), "lang": "fr-fr"}
        if month:
            p["month"] = month
        d = self._req("GET", LEDGER, "journal", params=p)
        return parse_ledger(d)

    def calendar(self) -> dict:
        """Événements en cours (avec leur date de fin) et bannières de vœux."""
        d = self._req("POST", f"{RECORD}/act_calendar", "calendrier",
                      json={"server": server_for(self.uid), "role_id": self.uid}, headers={"DS": _ds()})
        return parse_calendar(d)


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def parse_index(d: dict) -> dict:
    st = d.get("stats") or {}
    chests = {"ordinaires": _int(st.get("common_chest_number")), "délicats": _int(st.get("exquisite_chest_number")),
              "précieux": _int(st.get("precious_chest_number")), "luxueux": _int(st.get("luxurious_chest_number")),
              "étranges": _int(st.get("magic_chest_number"))}
    oculi = {k: _int(st.get(f"{k}_number")) for k in ("anemoculus", "geoculus", "electroculus", "dendroculus",
                                                       "hydroculus", "pyroculus", "moonoculus") if f"{k}_number" in st}
    regions = []
    for e in d.get("world_explorations") or []:
        if e.get("parent_id") not in (None, 0, "0"):
            continue   # sous-zones : on ne garde que les grandes régions
        regions.append({"id": _int(e.get("id")), "name": e.get("name", "?"),
                        "pct": _int(e.get("exploration_percentage")) / 10.0, "level": _int(e.get("level")),
                        "type": e.get("type", ""), "icon": e.get("icon") or e.get("inner_icon") or "",
                        "offerings": [(o.get("name", ""), _int(o.get("level"))) for o in e.get("offerings") or []]})
    regions.sort(key=lambda r: r["id"])
    return {"chests": chests, "oculi": oculi, "regions": regions,
            "achievements": _int(st.get("achievement_number")), "days": _int(st.get("active_day_number")),
            "waypoints": _int(st.get("way_point_number")), "domains": _int(st.get("domain_number")),
            "abyss": st.get("spiral_abyss", ""), "characters": _int(st.get("avatar_number"))}


def parse_ledger(d: dict) -> dict:
    m = d.get("month_data") or {}
    day = d.get("day_data") or {}
    cats = [{"name": c.get("action", "?"), "num": _int(c.get("num")), "pct": _int(c.get("percent"))}
            for c in m.get("group_by") or []]
    return {"month": _int(d.get("data_month") or d.get("month")), "nickname": d.get("nickname", ""),
            "primos": _int(m.get("current_primogems")), "mora": _int(m.get("current_mora")),
            "last_primos": _int(m.get("last_primogems")), "last_mora": _int(m.get("last_mora")),
            "primos_rate": _int(m.get("primogem_rate", m.get("primogems_rate"))), "mora_rate": _int(m.get("mora_rate")),
            "today_primos": _int(day.get("current_primogems")), "today_mora": _int(day.get("current_mora")),
            "categories": sorted(cats, key=lambda c: -c["num"]),
            "months": [_int(x) for x in d.get("optional_month") or []]}


def _ts(v) -> int:
    t = _int(v)
    return t // 1000 if t > 10 ** 12 else t


def parse_calendar(d: dict) -> dict:
    now = time.time()
    events = []
    for key in ("act_list", "fixed_act_list", "selected_act_list"):
        for a in d.get(key) or []:
            end = _ts(a.get("end_timestamp") or a.get("end_time"))
            if not end and a.get("countdown_seconds"):
                end = int(now + _int(a.get("countdown_seconds")))
            ev = {"id": str(a.get("id") or a.get("name")), "name": (a.get("name") or "?").strip(),
                  "start": _ts(a.get("start_timestamp") or a.get("start_time")), "end": end,
                  "finished": bool(a.get("is_finished")),
                  "desc": (a.get("desc") or "").strip(), "kind": key,
                  "rewards": [(r.get("name", ""), _int(r.get("num")), r.get("icon", "")) for r in a.get("reward_list") or []]}
            if ev["id"] not in {e["id"] for e in events}:
                events.append(ev)
    banners = []
    for key, what in (("avatar_card_pool_list", "avatars"), ("weapon_card_pool_list", "weapon")):
        for b in d.get(key) or []:
            items = b.get(what) or b.get("avatars") or b.get("weapon") or []
            if isinstance(items, dict):
                items = [items]
            banners.append({"name": b.get("pool_name") or b.get("name") or "Vœux",
                            "version": b.get("version_name", ""),
                            "start": _ts(b.get("start_timestamp")), "end": _ts(b.get("end_timestamp")) or (
                                int(now + _int(b.get("countdown_seconds"))) if b.get("countdown_seconds") else 0),
                            "items": [{"name": i.get("name", "?"), "icon": i.get("icon", ""),
                                       "rarity": _int(i.get("rarity") or i.get("rank"), 0)} for i in items],
                            "kind": "perso" if key.startswith("avatar") else "arme"})
    events.sort(key=lambda e: (e["finished"], e["end"] or 9e12))
    return {"events": events, "banners": banners, "fetched": now}


def parse_notes(d: dict) -> dict:
    now = time.time()

    def secs(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0
    exps = d.get("expeditions") or []
    tr = (d.get("transformer") or {})
    rt = tr.get("recovery_time") or {}
    tr_ready = bool(rt.get("reached")) if tr.get("obtained") else None
    daily = d.get("daily_task") or {}
    return {
        "fetched": now,
        "resin": int(d.get("current_resin") or 0), "max_resin": int(d.get("max_resin") or 200),
        "resin_full_at": now + secs(d.get("resin_recovery_time")),
        "tasks_done": int(d.get("finished_task_num") or 0), "tasks_total": int(d.get("total_task_num") or 4),
        "tasks_bonus": bool(d.get("is_extra_task_reward_received") or daily.get("is_extra_task_reward_received")),
        "boss_discounts": d.get("remain_resin_discount_num"),
        "exp_total": int(d.get("current_expedition_num") or len(exps)),
        "exp_max": int(d.get("max_expedition_num") or 5),
        "exp_done": sum(1 for e in exps if str(e.get("status", "")).lower() == "finished"),
        "exp_next_at": now + min((secs(e.get("remained_time")) for e in exps
                                  if str(e.get("status", "")).lower() != "finished"), default=0),
        "coins": int(d.get("current_home_coin") or 0), "max_coins": int(d.get("max_home_coin") or 0),
        "coins_full_at": now + secs(d.get("home_coin_recovery_time")),
        "transformer_ready": tr_ready,
    }


def resin_now(n: dict) -> int:
    """Résine estimée maintenant (entre deux mises à jour)."""
    if not n:
        return 0
    if time.time() >= n["resin_full_at"]:
        return n["max_resin"]
    left = (n["resin_full_at"] - time.time()) / 60 / RESIN_MINUTES
    return max(n["resin"], min(n["max_resin"], n["max_resin"] - int(left + 0.999)))


def duration(sec: float) -> str:
    sec = max(0, int(sec))
    h, m = sec // 3600, sec % 3600 // 60
    return f"{h} h {m:02d}" if h else f"{m} min"
