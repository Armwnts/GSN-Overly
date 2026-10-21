"""Actus de Genshin Impact : codes d'échange, annonces officielles HoYoLAB, personnages à venir, leaks.

Chaque source est indépendante : si l'une ne répond pas, les autres s'affichent quand même,
et la dernière version connue (cache disque) est utilisée hors ligne.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

ENNEAD = os.environ.get("GIO_CODES_URL", "https://api.ennead.cc/mihoyo/genshin/codes")
SERIA = os.environ.get("GIO_CODES_URL2", "https://hoyo-codes.seria.moe/codes?game=genshin")
BBS = os.environ.get("GIO_HOYOLAB_BBS", "https://bbs-api-os.hoyolab.com") + "/community/post/wapi/getNewsList"
REDDIT = os.environ.get("GIO_REDDIT_BASE", "https://www.reddit.com") + "/r/Genshin_Impact_Leaks"
WIKI_API = os.environ.get("GIO_WIKI_API", "https://genshin-impact.fandom.com/api.php")
REDEEM_URL = "https://genshin.hoyoverse.com/fr/gift?code={code}"
ARTICLE_URL = "https://www.hoyolab.com/article/{id}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) GenshinOverlay/5.0 (outil personnel)",
           "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5"}
MAX_AGE = 30 * 60
NEWS_TYPES = {"infos": 3, "events": 2, "notices": 1}

REWARDS_FR = [("Primogem", "Primo-gemme"), ("Hero's Wit", "Expérience du héros"),
              ("Adventurer's Experience", "EXP d'aventure"), ("Mystic Enhancement Ore", "Minerai de raffinage mystique"),
              ("Fine Enhancement Ore", "Minerai de raffinage fin"), ("Enhancement Ore", "Minerai de raffinage"),
              ("Wanderer's Advice", "Conseil du vagabond"), ("Mora", "Mora")]


class NewsError(RuntimeError):
    pass


def _fr_reward(s: str) -> str:
    s = s.strip().replace("*", " ×").replace(" x", " ×")
    for en, fr in REWARDS_FR:
        s = re.sub(re.escape(en) + r"s?\b", fr, s)
    return re.sub(r"\s+×\s*", " ×", s)


def parse_codes_ennead(d: dict) -> list[dict]:
    return [{"code": c["code"].strip(), "rewards": [_fr_reward(r) for r in (c.get("rewards") or [])]}
            for c in d.get("active") or [] if c.get("code")]


def parse_codes_seria(d: dict) -> list[dict]:
    out = []
    for c in d.get("codes") or []:
        if c.get("code") and str(c.get("status", "OK")).upper() == "OK":
            rew = [_fr_reward(r) for r in str(c.get("rewards") or "").split(";") if r.strip()]
            out.append({"code": c["code"].strip(), "rewards": rew})
    return out


def parse_news(d: dict) -> list[dict]:
    out = []
    for it in ((d.get("data") or {}).get("list") or []):
        p = it.get("post") or {}
        if not p.get("post_id"):
            continue
        cover = (it.get("cover") or {}).get("url") or p.get("cover") or ""
        if not cover and it.get("image_list"):
            cover = it["image_list"][0].get("url", "")
        out.append({"id": str(p["post_id"]), "title": (p.get("subject") or "").strip(),
                    "date": int(p.get("created_at") or 0), "cover": cover,
                    "desc": re.sub(r"\s+", " ", p.get("desc") or p.get("content") or "")[:220],
                    "url": ARTICLE_URL.format(id=p["post_id"])})
    return out


def parse_reddit_json(d: dict) -> list[dict]:
    out = []
    for ch in ((d.get("data") or {}).get("children") or []):
        p = ch.get("data") or {}
        if not p.get("title") or p.get("stickied"):
            continue
        out.append({"title": p["title"], "flair": p.get("link_flair_text") or "",
                    "date": int(p.get("created_utc") or 0), "comments": p.get("num_comments", 0),
                    "score": p.get("score", 0),
                    "url": "https://www.reddit.com" + p.get("permalink", "") if p.get("permalink") else p.get("url", "")})
    return out


def parse_reddit_rss(xml: str) -> list[dict]:
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml)
    out = []
    for e in root.findall("a:entry", ns):
        link = e.find("a:link", ns)
        upd = (e.findtext("a:updated", "", ns) or e.findtext("a:published", "", ns))[:19]
        try:
            ts = calendar.timegm(time.strptime(upd, "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            ts = 0
        cat = e.find("a:category", ns)
        out.append({"title": e.findtext("a:title", "", ns), "flair": "",
                    "date": ts, "comments": None, "score": None,
                    "url": link.get("href") if link is not None else "",
                    "_cat": cat.get("term") if cat is not None else ""})
    return out


class NewsStore:
    def __init__(self, folder: Path):
        self.dir = folder
        self.dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _cached(self, name: str, fetch, refresh=False):
        f = self.dir / f"{name}.json"
        if f.exists() and not refresh:
            d = json.loads(f.read_text(encoding="utf-8"))
            if time.time() - d.get("fetched", 0) < MAX_AGE:
                return d["items"]
        try:
            items = fetch()
        except NewsError:
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))["items"]
            raise
        f.write_text(json.dumps({"fetched": time.time(), "items": items}, ensure_ascii=False), encoding="utf-8")
        return items

    def _get(self, url, **kw):
        try:
            r = self.session.get(url, timeout=20, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            raise NewsError(type(e).__name__) from e

    # ------------------------------------------------------------------ codes
    def codes(self, refresh=False) -> list[dict]:
        def fetch():
            errs = []
            for url, parse in ((ENNEAD, parse_codes_ennead), (SERIA, parse_codes_seria)):
                try:
                    return parse(self._get(url).json())
                except (NewsError, ValueError, KeyError, TypeError) as e:
                    errs.append(str(e))
            raise NewsError("Les sites de codes ne répondent pas pour le moment.")
        return self._cached("codes", fetch, refresh)

    # ------------------------------------------------------------------ actus officielles
    def news(self, kind: str = "infos", refresh=False) -> list[dict]:
        def fetch():
            try:
                r = self._get(BBS, params={"gids": 2, "type": NEWS_TYPES[kind], "page_size": 20},
                              headers={"x-rpc-language": "fr-fr", "Referer": "https://www.hoyolab.com/"})
                items = parse_news(r.json())
            except (ValueError, KeyError, TypeError) as e:
                raise NewsError("Réponse inattendue de HoYoLAB.") from e
            except NewsError as e:
                raise NewsError("HoYoLAB ne répond pas pour le moment.") from e
            return items
        return self._cached(f"news_{kind}", fetch, refresh)

    # ------------------------------------------------------------------ à venir
    def upcoming(self, characters: list[dict] | None, refresh=False) -> list[dict]:
        """Personnages dont la sortie est annoncée : date de sortie future dans la base de données
        du jeu, et catégorie « Upcoming Characters » du wiki (en anglais)."""
        now = time.time()
        out = [{"name": c["name"], "element": c.get("element", ""), "rank": c.get("rank"),
                "weapon": c.get("weapon", ""), "date": c["release"], "icon": c.get("icon", ""), "source": "base de données"}
               for c in (characters or []) if (c.get("release") or 0) > now]

        def fetch():
            try:
                r = self._get(WIKI_API, params={"action": "query", "list": "categorymembers",
                                                "cmtitle": "Category:Upcoming Characters", "cmlimit": 50,
                                                "cmnamespace": 0, "format": "json"})
                return [m["title"] for m in r.json().get("query", {}).get("categorymembers", [])]
            except (ValueError, KeyError, TypeError) as e:
                raise NewsError("wiki") from e
        try:
            wiki = self._cached("upcoming_wiki", fetch, refresh)
        except NewsError:
            wiki = []
        known = {re.sub(r"\W", "", o["name"].lower()) for o in out}
        for t in wiki:
            if re.sub(r"\W", "", t.lower()) not in known:
                out.append({"name": t, "element": "", "rank": None, "weapon": "", "date": 0, "icon": "",
                            "source": "wiki (anglais)"})
        return sorted(out, key=lambda o: (o["date"] or 9e12, o["name"]))

    # ------------------------------------------------------------------ leaks
    def leaks(self, refresh=False) -> list[dict]:
        def fetch():
            try:
                return parse_reddit_json(self._get(REDDIT + "/new.json", params={"limit": 40, "raw_json": 1}).json())
            except (NewsError, ValueError, KeyError, TypeError):
                pass
            try:
                return parse_reddit_rss(self._get(REDDIT + "/new/.rss").text)
            except (NewsError, ET.ParseError) as e:
                raise NewsError("Reddit ne répond pas pour le moment.") from e
        return self._cached("leaks", fetch, refresh)

    def image(self, url: str) -> Path:
        name = re.sub(r"[^\w.-]", "_", url.rsplit("/", 1)[-1].split("?")[0])[-80:] or "img"
        p = self.dir / "img" / name
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(self._get(url).content)
        return p
