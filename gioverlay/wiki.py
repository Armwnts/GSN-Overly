"""Recherche de soluces sur le wiki Genshin Impact (Fandom), gratuit et sans clé.

Les pages de quête du wiki anglais contiennent les étapes (« Steps ») et un tableau
« Other Languages » avec le nom français : une recherche avec le titre français
retrouve donc la bonne page.
"""
from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from urllib.parse import quote

import requests

API = os.environ.get("GIO_WIKI_API", "https://genshin-impact.fandom.com/api.php")
PAGE_URL = "https://genshin-impact.fandom.com/wiki/"
HEADERS = {"User-Agent": "GenshinOverlay/2.1 (outil personnel d'aide de quete)"}
MAX_CHARS = 9000

_SKIP_CLASSES = ("navbox", "toc", "reference", "mw-editsection", "gallery", "portable-infobox",
                 "mw-references-wrap", "mw-collapsible")
_BLOCKS = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "dd", "dt", "br", "table", "ul", "ol"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.stack: list[bool] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "img", "hr", "input", "meta", "link", "wbr", "source"):
            if tag == "br":
                self.out.append("\n")
            return
        cls = dict(attrs).get("class") or ""
        skip = tag in ("script", "style", "sup", "figure") or any(c in cls for c in _SKIP_CLASSES)
        self.stack.append(skip)
        if skip:
            self.skip += 1
        if not self.skip:
            if tag in ("h2", "h3", "h4"):
                self.out.append("\n\n## ")
            elif tag == "li":
                self.out.append("\n- ")
            elif tag in ("td", "th"):
                self.out.append(" | ")
            elif tag in _BLOCKS:
                self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("br", "img", "hr", "input", "meta", "link", "wbr", "source") or not self.stack:
            return
        if self.stack.pop():
            self.skip -= 1
        if not self.skip and tag in _BLOCKS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = "".join(p.out)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Les sections sans intérêt pour la soluce
    text = re.split(r"\n## (?:Change History|Other Languages|Navigation|References|Trivia)\b", text)[0]
    return text.strip()


def search(query: str, limit: int = 3) -> list[str]:
    if not query.strip():
        return []
    r = requests.get(API, params={"action": "query", "list": "search", "srsearch": query,
                                  "srlimit": limit, "srnamespace": 0, "format": "json"},
                     headers=HEADERS, timeout=15)
    r.raise_for_status()
    return [h["title"] for h in r.json().get("query", {}).get("search", [])]


def page(title: str) -> dict:
    r = requests.get(API, params={"action": "parse", "page": title, "prop": "text", "redirects": 1,
                                  "disableeditsection": 1, "format": "json"}, headers=HEADERS, timeout=20)
    r.raise_for_status()
    p = r.json().get("parse") or {}
    real = p.get("title", title)
    text = html_to_text((p.get("text") or {}).get("*", ""))
    return {"title": real, "url": PAGE_URL + quote(real.replace(" ", "_")), "text": text[:MAX_CHARS]}


def find_quest_pages(queries: list[str], max_pages: int = 2) -> list[dict]:
    """Essaie chaque requête (titre français exact, titre anglais supposé…) et
    renvoie le texte des meilleures pages trouvées. Ne lève jamais d'exception."""
    titles: list[str] = []
    for q in queries:
        try:
            for t in search(q):
                if t not in titles:
                    titles.append(t)
        except (requests.RequestException, ValueError):
            continue
        if len(titles) >= max_pages:
            break
    pages = []
    for t in titles[:max_pages]:
        try:
            pg = page(t)
            if pg["text"]:
                pages.append(pg)
        except (requests.RequestException, ValueError):
            continue
    return pages
