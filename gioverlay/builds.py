"""Guides de builds de sephijin.fr : liste des personnages et fiche de chacun.

Le contenu appartient à sephijin.fr : il est téléchargé quand l'utilisateur le consulte
(comme le ferait un navigateur), gardé en cache sur son ordinateur, et toujours affiché
avec le lien vers la page d'origine.
"""
from __future__ import annotations

import json
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

BASE = os.environ.get("GIO_SEPHIJIN_BASE", "https://sephijin.fr")  # variable : pour les tests
INDEX_URL = BASE + "/genshin-impact-personnages/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) GenshinOverlay/4.0 (outil personnel)",
           "Accept-Language": "fr-FR,fr;q=0.9"}
INDEX_MAX_AGE = 3 * 24 * 3600      # la liste des personnages est rafraîchie tous les 3 jours
PAGE_MAX_AGE = 2 * 24 * 3600       # une fiche est rafraîchie tous les 2 jours

_SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "form", "svg", "button", "select"}
_BLOCKS = {"p", "li", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "blockquote", "dd", "dt"}
_IGNORED_SECTIONS = ("besoin d'aide", "autres guides", "commentaire", "partager", "newsletter")


class BuildError(RuntimeError):
    pass


def _img_url(attrs: dict) -> str | None:
    """URL réelle d'une image (les sites WordPress chargent souvent les images en différé)."""
    for k in ("data-lazy-src", "data-src", "data-orig-file", "src"):
        v = attrs.get(k)
        if v and not v.startswith("data:"):
            return v
    for k in ("data-lazy-srcset", "data-srcset", "srcset"):
        v = attrs.get(k)
        if v:
            best = max((c.strip().split(" ") for c in v.split(",") if c.strip()),
                       key=lambda c: int(re.sub(r"\D", "", c[1]) or 0) if len(c) > 1 else 0)
            return best[0]
    return None


# --------------------------------------------------------------------------- liste
class _IndexParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.skip = 0
        self.a: dict | None = None
        self.out: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in _SKIP_TAGS:
            self.skip += 1
        if self.skip:
            return
        if tag == "a" and a.get("href"):
            self.a = {"href": urljoin(self.base, a["href"]), "text": "", "imgs": []}
        elif tag == "img" and self.a is not None:
            self.a["imgs"].append((_img_url(a) or "", a.get("alt") or ""))

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self.skip:
            self.skip -= 1
            return
        if tag == "a" and self.a is not None:
            self.out.append(self.a)
            self.a = None

    def handle_data(self, data):
        if self.a is not None and not self.skip:
            self.a["text"] += data


def _name_from_alt(alt: str) -> str:
    s = re.sub(r"^pp-(list-)?", "", alt.strip().lower())
    return s.replace("-", " ").replace("_", " ").title()


def parse_index(html: str, base: str = INDEX_URL) -> list[dict]:
    p = _IndexParser(base)
    p.feed(html)
    chars, seen = [], set()
    host = urlparse(BASE).netloc
    for a in p.out:
        imgs = [(u, alt) for u, alt in a["imgs"] if "pp-" in (alt + " " + u).lower()]
        if not imgs or urlparse(a["href"]).netloc not in ("", host):
            continue
        url = a["href"].split("#")[0].rstrip("/") + "/"
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        if not slug or url in seen or slug.startswith("genshin-impact"):
            continue
        seen.add(url)
        name = re.sub(r"\s+", " ", a["text"]).strip() or _name_from_alt(imgs[0][1])
        thumb = urljoin(base, imgs[0][0]) if imgs[0][0] else ""
        chars.append({"name": name, "slug": slug, "url": url, "thumb": thumb})
    return sorted(chars, key=lambda c: c["name"].lower())


# --------------------------------------------------------------------------- fiche
class _PageParser(HTMLParser):
    """Aplatit la page en une suite de blocs (titres, paragraphes, images) dans l'ordre."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.skip = 0
        self.blocks: list[dict] = []
        self.cur: dict | None = None
        self.depth = 0
        self.bold = 0
        self.href: str | None = None
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            k = a.get("property") or a.get("name")
            if k and a.get("content"):
                self.meta[k] = a["content"]
            return
        if tag in _SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "a":
            self.href = urljoin(self.base, a["href"]) if a.get("href") else None
        elif tag in ("strong", "b"):
            self.bold += 1
        elif tag == "img":
            u = _img_url(a)
            if u:
                self.blocks.append({"t": "img", "src": urljoin(self.base, u), "href": self.href,
                                    "alt": a.get("alt") or ""})
        elif tag == "br" and self.cur is not None:
            self.cur["text"] += "\n"
        if tag in _BLOCKS:
            if self.cur is not None and tag not in ("li", "td", "th"):
                self._close()
            if self.cur is None:
                self.cur = {"t": tag, "text": "", "bold_chars": 0}
                self.depth = 0
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag == "a":
            self.href = None
        elif tag in ("strong", "b") and self.bold:
            self.bold -= 1
        if tag in _BLOCKS and self.cur is not None:
            self.depth -= 1
            if self.depth <= 0:
                self._close()

    def handle_data(self, data):
        if self.skip or self.cur is None:
            return
        self.cur["text"] += data
        if self.bold:
            self.cur["bold_chars"] += len(data.strip())

    def _close(self):
        c, self.cur = self.cur, None
        c["text"] = re.sub(r"[ \t ]+", " ", c["text"]).strip()
        c["text"] = re.sub(r" *\n+ *", "\n", c["text"])
        if c["text"]:
            c["bold"] = c["bold_chars"] >= 0.8 * len(re.sub(r"\s", "", c["text"]))
            self.blocks.append(c)


def parse_character(html: str, url: str) -> dict:
    p = _PageParser(url)
    p.feed(html)
    if p.cur is not None:
        p._close()
    blocks = p.blocks
    h1 = next((b["text"] for b in blocks if b["t"] == "h1"), "")
    name = re.split(r"\s*[:|–-]\s*(le )?guide", h1, flags=re.I)[0].strip() or h1

    # Image(s) de synthèse du build
    synth: list[str] = []
    for b in blocks:
        if b["t"] != "img":
            continue
        for u in (b.get("href"), b["src"]):
            if u and "synthese" in u.lower() and re.search(r"\.(webp|png|jpe?g)(\?|$)", u.lower()):
                full = re.sub(r"-\d+x\d+(?=\.\w+$)", "", u)
                if full not in synth:
                    synth.append(full)
                break
    if not synth:
        guess = [b["src"] for b in blocks if b["t"] == "img" and "guide" in b["src"].lower()]
        synth = guess[:1] or ([p.meta["og:image"]] if p.meta.get("og:image") else [])

    # Résumé : paragraphes en gras entre le titre et la première section
    i_h1 = next((i for i, b in enumerate(blocks) if b["t"] == "h1"), -1)
    i_h2 = next((i for i, b in enumerate(blocks) if b["t"] in ("h2", "h3") and i > i_h1), len(blocks))
    intro = [b for b in blocks[i_h1 + 1:i_h2] if b["t"] != "img"]
    summary = [b["text"] for b in intro if b.get("bold") and len(b["text"]) > 40]
    if not summary:
        summary = [b["text"] for b in intro if len(b["text"]) > 60][:2]
    if not summary and p.meta.get("og:description"):
        summary = [p.meta["og:description"]]
    updated = next((b["text"] for b in intro if b["text"].lower().startswith("mis à jour")), "")

    # Sections : texte sous chaque titre, jusqu'au titre suivant
    sections: list[dict] = []
    cur = None
    for b in blocks[i_h2:]:
        if b["t"] == "img":
            continue
        if b["t"] in ("h2", "h3"):
            cur = {"title": b["text"], "lines": []}
            sections.append(cur)
        elif cur is not None:
            line = b["text"] if b["t"] != "h4" else f"▸ {b['text']}"
            if line and (not cur["lines"] or cur["lines"][-1] != line):
                cur["lines"].append(line)
    sections = [s for s in sections if s["lines"] and not any(k in s["title"].lower() for k in _IGNORED_SECTIONS)]
    for s in sections:
        text, out = 0, []
        for ln in s["lines"]:
            text += len(ln)
            if text > 2500:
                break
            out.append(ln)
        s["lines"] = out
    return {"name": name, "title": h1, "url": url, "summary": summary, "updated": updated,
            "synth": synth, "sections": sections, "fetched": time.time()}


def guide_text(page: dict, max_chars: int = 9000) -> str:
    """Texte du guide, pour le donner à Gemini comme contexte."""
    out = [f"Personnage : {page['name']}", f"Source : {page['url']}"]
    out += page.get("summary", [])
    for s in page.get("sections", []):
        out.append(f"\n## {s['title']}")
        out += s["lines"]
    return "\n".join(out)[:max_chars]


# --------------------------------------------------------------------------- cache
class BuildStore:
    def __init__(self, folder: Path):
        self.dir = folder
        self.dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _get(self, url: str, binary=False):
        try:
            r = self.session.get(url, timeout=25)
            r.raise_for_status()
        except requests.RequestException as e:
            raise BuildError(f"Impossible de joindre sephijin.fr ({type(e).__name__}).") from e
        return r.content if binary else r.text

    def characters(self, refresh=False) -> list[dict]:
        f = self.dir / "index.json"
        if f.exists() and not refresh:
            d = json.loads(f.read_text(encoding="utf-8"))
            if time.time() - d.get("fetched", 0) < INDEX_MAX_AGE and d.get("characters"):
                return d["characters"]
        try:
            chars = parse_index(self._get(INDEX_URL))
        except BuildError:
            if f.exists():  # hors ligne : on garde la dernière liste connue
                return json.loads(f.read_text(encoding="utf-8")).get("characters", [])
            raise
        if not chars:
            raise BuildError("La liste des personnages de sephijin.fr n'a pas pu être lue "
                             "(le site a peut-être changé).")
        f.write_text(json.dumps({"fetched": time.time(), "characters": chars}, ensure_ascii=False), encoding="utf-8")
        return chars

    def page(self, char: dict, refresh=False) -> dict:
        d = self.dir / char["slug"]
        f = d / "page.json"
        if f.exists() and not refresh:
            pg = json.loads(f.read_text(encoding="utf-8"))
            if time.time() - pg.get("fetched", 0) < PAGE_MAX_AGE:
                return pg
        try:
            pg = parse_character(self._get(char["url"]), char["url"])
        except BuildError:
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
            raise
        pg["name"] = char.get("name") or pg["name"]
        d.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(pg, ensure_ascii=False), encoding="utf-8")
        return pg

    def image(self, url: str, slug: str) -> Path:
        name = re.sub(r"[^\w.-]", "_", url.rsplit("/", 1)[-1].split("?")[0]) or "image"
        p = self.dir / slug / name
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(self._get(url, binary=True))
        return p
