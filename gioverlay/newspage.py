"""Page « News » : codes d'échange, actus officielles HoYoLAB, personnages à venir et leaks."""
from __future__ import annotations

import time

from PyQt6.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

from .gamedata import ELEMENT_COLORS
from .news import REDEEM_URL, NewsStore
from .uikit import COMMON_STYLE, ClickFrame, Job, JobOwner, load_image, when

STYLE = COMMON_STYLE + """
QWidget#news { background: #111318; }
QWidget#col { background: transparent; }
QLabel { color: #e8e6e3; }
QLabel#code { font-family: Menlo, Consolas, monospace; font-size: 17px; font-weight: 700; color: #f5c542; }
QLabel#title { font-size: 13px; font-weight: 600; }
QLabel#tag { color: #16181d; background: #f5c542; border-radius: 6px; padding: 1px 6px; font-size: 10px; font-weight: 700; }
QLabel#warn { color: #e0a84a; font-size: 12px; }
QLabel#cover { background: #0d0f13; border-radius: 6px; }
QPushButton { background: #2a2e37; color: #e8e6e3; border: 1px solid #383d48; border-radius: 7px; padding: 5px 10px; }
QPushButton:hover { background: #333845; }
QPushButton#go { background: #f5c542; color: #16181d; border: none; font-weight: 700; }
QCheckBox { color: #a7abb3; font-size: 11px; }
"""


class ImgJob(QThread):
    got = pyqtSignal(int, object)

    def __init__(self, store: NewsStore, urls: list[tuple[int, str]], loader=None):
        super().__init__()
        self.store, self.urls, self.loader = store, urls, loader

    def run(self):
        for i, url in self.urls:
            if self.isInterruptionRequested():
                return
            try:
                p = self.loader(url) if self.loader else self.store.image(url)
                img = load_image(p) if p else None
                if img is not None and not img.isNull():
                    self.got.emit(i, img)
            except Exception:  # noqa: BLE001 — image manquante : pas grave
                continue


def _column(title: str) -> tuple[QWidget, QVBoxLayout, QLabel, QVBoxLayout]:
    """Colonne : titre, ligne d'état, zone défilante. Renvoie (widget, layout d'en-tête, état, layout des cartes)."""
    col = QWidget()
    col.setObjectName("col")
    v = QVBoxLayout(col)
    v.setContentsMargins(0, 0, 0, 0)
    head = QVBoxLayout()
    h = QLabel(title)
    h.setObjectName("h")
    head.addWidget(h)
    v.addLayout(head)
    status = QLabel()
    status.setObjectName("muted")
    status.setWordWrap(True)
    v.addWidget(status)
    inner = QWidget()
    inner.setObjectName("col")
    cards = QVBoxLayout(inner)
    cards.setContentsMargins(0, 4, 6, 4)
    cards.setSpacing(8)
    cards.addStretch(1)
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sc.setWidget(inner)
    v.addWidget(sc, 1)
    return col, head, status, cards


def _clear(lay: QVBoxLayout):
    while lay.count() > 1:  # garde le « stretch » final
        w = lay.takeAt(0).widget()
        if w:
            w.setParent(None)
            w.deleteLater()


def _open(url: str):
    if url:
        QDesktopServices.openUrl(QUrl(url))


class NewsPage(QWidget, JobOwner):
    def __init__(self, store: NewsStore, gamedata, roster):
        super().__init__()
        self.setObjectName("news")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.store, self.gamedata, self.roster = store, gamedata, roster
        self.kind = "infos"
        self._loaded = False
        self._req = {"news": 0}
        self.covers: dict[int, QLabel] = {}

        top = QHBoxLayout()
        title = QLabel("Actus Genshin Impact")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #f5c542;")
        top.addWidget(title)
        top.addStretch(1)
        self.updated = QLabel()
        self.updated.setObjectName("muted")
        top.addWidget(self.updated)
        b = QPushButton("⟳ Tout actualiser")
        b.clicked.connect(lambda: self.load_all(refresh=True))
        top.addWidget(b)

        # --- codes ----------------------------------------------------------------
        c1, _h1, self.codes_status, self.codes_box = _column("🎁 Codes d'échange")
        c1.setMinimumWidth(270)
        # --- actus officielles --------------------------------------------------
        c2, h2, self.news_status, self.news_box = _column("📰 Actus officielles")
        pills = QHBoxLayout()
        self.kinds = QButtonGroup(self)
        for i, (k, lab) in enumerate((("infos", "Infos"), ("events", "Événements"), ("notices", "Annonces"))):
            p = QPushButton(lab)
            p.setObjectName("pill")
            p.setCheckable(True)
            p.setChecked(k == self.kind)
            p.clicked.connect(lambda _=False, k=k: self.load_news(k))
            self.kinds.addButton(p, i)
            pills.addWidget(p)
        pills.addStretch(1)
        h2.addLayout(pills)
        # --- à venir + leaks ------------------------------------------------------
        c3, _h3, self.up_status, self.up_box = _column("🔮 Personnages à venir")
        c3.setMinimumWidth(270)
        c4, h4, self.leak_status, self.leak_box = _column("🕵 Leaks")
        self.show_leaks = QCheckBox("Afficher les leaks (spoilers, infos non officielles)")
        self.show_leaks.toggled.connect(self._leaks_toggled)
        h4.addWidget(self.show_leaks)
        right = QVBoxLayout()
        right.addWidget(c3, 2)
        right.addWidget(c4, 3)
        rw = QWidget()
        rw.setObjectName("col")
        rw.setLayout(right)
        right.setContentsMargins(0, 0, 0, 0)

        cols = QHBoxLayout()
        cols.setSpacing(18)
        cols.addWidget(c1, 3)
        cols.addWidget(c2, 5)
        cols.addWidget(rw, 4)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.addLayout(top)
        lay.addLayout(cols, 1)
        credit = QLabel("Sources : HoYoLAB (actus officielles), api.ennead.cc / hoyo-codes.seria.moe (codes), "
                        "Project Yatta et wiki Genshin (à venir), r/Genshin_Impact_Leaks (leaks).")
        credit.setObjectName("muted")
        lay.addWidget(credit)
        self.leak_status.setText("Masqués pour éviter les spoilers.")

    def showEvent(self, e):
        super().showEvent(e)
        if not self._loaded:
            self._loaded = True
            self.load_all()

    def load_all(self, refresh=False):
        self.load_codes(refresh)
        self.load_news(self.kind, refresh)
        self.load_upcoming(refresh)
        if self.show_leaks.isChecked():
            self.load_leaks(refresh)
        self.updated.setText("Mis à jour " + time.strftime("à %H:%M"))

    # ------------------------------------------------------------------ codes
    def load_codes(self, refresh=False):
        self.codes_status.setText("Chargement…")
        self._run(Job(self.store.codes, refresh), self._codes_loaded,
                  lambda m: self.codes_status.setText(f"⚠ {m}"))

    def _codes_loaded(self, codes):
        _clear(self.codes_box)
        used = self.roster.used_codes
        codes = sorted(codes, key=lambda c: c["code"] in used)
        new = sum(1 for c in codes if c["code"] not in used)
        self.codes_status.setText(f"{len(codes)} code(s) actif(s) · {new} pas encore utilisé(s)" if codes
                                  else "Aucun code actif pour le moment.")
        for c in codes:
            self.codes_box.insertWidget(self.codes_box.count() - 1, self._code_card(c, c["code"] in used))

    def _code_card(self, c, is_used):
        card = ClickFrame(clickable=False)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        lab = QLabel(c["code"])
        lab.setObjectName("code")
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(lab)
        r = QLabel(" · ".join(c.get("rewards") or []) or "Récompenses non précisées")
        r.setWordWrap(True)
        r.setStyleSheet("color: #c9c6c1; font-size: 12px;")
        v.addWidget(r)
        row = QHBoxLayout()
        go = QPushButton("Utiliser ↗")
        go.setObjectName("go")
        go.setToolTip("Ouvre la page officielle d'échange avec le code déjà rempli")
        go.clicked.connect(lambda: (_open(REDEEM_URL.format(code=c["code"])), done.setChecked(True)))
        cp = QPushButton("Copier")
        cp.clicked.connect(lambda: (QGuiApplication.clipboard().setText(c["code"]), cp.setText("Copié ✓")))
        done = QCheckBox("Déjà utilisé")
        done.setChecked(is_used)
        done.toggled.connect(lambda on: self._mark_code(c["code"], on, card))
        row.addWidget(go)
        row.addWidget(cp)
        row.addStretch(1)
        row.addWidget(done)
        v.addLayout(row)
        card.setStyleSheet("QFrame#card { background: #15171c; }" if is_used else "")
        if is_used:
            lab.setStyleSheet("color: #6b6f78; text-decoration: line-through;")
        return card

    def _mark_code(self, code, on, card):
        (self.roster.used_codes.add if on else self.roster.used_codes.discard)(code)
        self.roster.save()
        card.setStyleSheet("QFrame#card { background: #15171c; }" if on else "")
        card.findChild(QLabel, "code").setStyleSheet("color: #6b6f78; text-decoration: line-through;" if on else "")

    # ------------------------------------------------------------------ actus
    def load_news(self, kind, refresh=False):
        self.kind = kind
        self._req["news"] += 1
        req = self._req["news"]
        self.news_status.setText("Chargement…")
        self._run(Job(self.store.news, kind, refresh), lambda items: self._news_loaded(req, items),
                  lambda m: req == self._req["news"] and self.news_status.setText(f"⚠ {m}"))

    def _news_loaded(self, req, items):
        if req != self._req["news"]:
            return
        _clear(self.news_box)
        self.covers = {}
        self.news_status.setText(f"{len(items)} article(s) · clique pour lire sur HoYoLAB" if items
                                 else "Aucun article.")
        urls = []
        for i, it in enumerate(items):
            card = ClickFrame()
            card.clicked.connect(lambda u=it["url"]: _open(u))
            h = QHBoxLayout(card)
            h.setContentsMargins(8, 8, 10, 8)
            cov = QLabel()
            cov.setObjectName("cover")
            cov.setFixedSize(150, 84)
            cov.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.addWidget(cov)
            v = QVBoxLayout()
            t = QLabel(it["title"])
            t.setObjectName("title")
            t.setWordWrap(True)
            v.addWidget(t)
            if it.get("desc"):
                d = QLabel(it["desc"][:140] + ("…" if len(it["desc"]) > 140 else ""))
                d.setWordWrap(True)
                d.setStyleSheet("color: #a7abb3; font-size: 11px;")
                v.addWidget(d)
            dt = QLabel(when(it["date"]))
            dt.setObjectName("muted")
            v.addWidget(dt)
            v.addStretch(1)
            h.addLayout(v, 1)
            self.news_box.insertWidget(self.news_box.count() - 1, card)
            if it.get("cover"):
                self.covers[i] = cov
                urls.append((i, it["cover"]))
        job = ImgJob(self.store, urls)
        job.got.connect(lambda i, img, r=req: self._cover(r, i, img))
        self._run(job)

    def _cover(self, req, i, img):
        lab = self.covers.get(i)
        if req == self._req["news"] and lab is not None:
            lab.setPixmap(QPixmap.fromImage(img).scaled(lab.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                        Qt.TransformationMode.SmoothTransformation)
                          .copy(0, 0, lab.width(), lab.height()))

    # ------------------------------------------------------------------ à venir
    def load_upcoming(self, refresh=False):
        self.up_status.setText("Chargement…")

        def fetch():
            try:
                chars = self.gamedata.characters(refresh)
            except Exception:  # noqa: BLE001
                chars = []
            return self.store.upcoming(chars, refresh)
        self._run(Job(fetch), self._up_loaded, lambda m: self.up_status.setText(f"⚠ {m}"))

    def _up_loaded(self, items):
        _clear(self.up_box)
        self.up_status.setText("Annoncés officiellement ou présents dans les fichiers du jeu."
                               if items else "Aucun personnage à venir connu pour l'instant.")
        icons = []
        self._up_icons = {}
        for i, it in enumerate(items):
            card = ClickFrame(clickable=False)
            h = QHBoxLayout(card)
            h.setContentsMargins(8, 6, 10, 6)
            ic = QLabel()
            ic.setFixedSize(44, 44)
            h.addWidget(ic)
            v = QVBoxLayout()
            stars = "★" * (it["rank"] or 0)
            n = QLabel(f"{it['name']}  <span style='color:#f5c542'>{stars}</span>")
            n.setObjectName("title")
            v.addWidget(n)
            col = ELEMENT_COLORS.get(it.get("element"), "#a7abb3")
            info = " · ".join(x for x in (f"<span style='color:{col}'>{it['element']}</span>" if it.get("element") else "",
                                          it.get("weapon", ""), when(it["date"]) if it["date"] else "date inconnue") if x)
            sub = QLabel(f"{info}<br><span style='color:#6b6f78'>source : {it['source']}</span>")
            sub.setStyleSheet("font-size: 11px; color: #a7abb3;")
            v.addWidget(sub)
            h.addLayout(v, 1)
            self.up_box.insertWidget(self.up_box.count() - 1, card)
            if it.get("icon"):
                self._up_icons[i] = ic
                icons.append((i, it["icon"]))
        job = ImgJob(self.store, icons, loader=self.gamedata.icon)
        job.got.connect(lambda i, img: self._up_icons.get(i) and self._up_icons[i].setPixmap(
            QPixmap.fromImage(img).scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)))
        self._run(job)

    # ------------------------------------------------------------------ leaks
    def _leaks_toggled(self, on):
        if on:
            self.load_leaks()
        else:
            _clear(self.leak_box)
            self.leak_status.setText("Masqués pour éviter les spoilers.")

    def load_leaks(self, refresh=False):
        self.leak_status.setText("Chargement…")
        self._run(Job(self.store.leaks, refresh), self._leaks_loaded,
                  lambda m: self.leak_status.setText(f"⚠ {m}"))

    def _leaks_loaded(self, items):
        if not self.show_leaks.isChecked():
            return
        _clear(self.leak_box)
        self.leak_status.setText("Infos NON officielles, souvent incomplètes ou fausses. "
                                 "Les tags « Reliable » / « Questionable » viennent du sous-reddit.")
        for it in items[:40]:
            card = ClickFrame()
            card.clicked.connect(lambda u=it["url"]: _open(u))
            v = QVBoxLayout(card)
            v.setContentsMargins(10, 7, 10, 7)
            row = QHBoxLayout()
            if it.get("flair"):
                tag = QLabel(it["flair"][:24])
                fl = it["flair"].lower()
                bg = "#f5c542"
                if "reliable" in fl and "un" not in fl:
                    bg = "#74c2a8"
                elif "questionable" in fl or "unreliable" in fl:
                    bg = "#e07a5f"
                tag.setStyleSheet(f"color: #16181d; background: {bg}; border-radius: 6px; padding: 1px 6px; "
                                  "font-size: 10px; font-weight: 700;")
                tag.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
                row.addWidget(tag)
            d = QLabel(when(it["date"]) + (f" · {it['comments']} comm." if it.get("comments") is not None else ""))
            d.setObjectName("muted")
            row.addWidget(d)
            row.addStretch(1)
            v.addLayout(row)
            t = QLabel(it["title"])
            t.setWordWrap(True)
            t.setStyleSheet("font-size: 12px;")
            v.addWidget(t)
            self.leak_box.insertWidget(self.leak_box.count() - 1, card)
