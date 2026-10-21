"""Page « Compte » (HoYoLAB) : exploration officielle par région, journal du voyageur
(primo-gemmes et Mora du mois) et calendrier des événements."""
from __future__ import annotations

import time

import requests
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
                             QVBoxLayout, QWidget)

from .uikit import COMMON_STYLE, ClickFrame, Job, JobOwner, load_image

STYLE = COMMON_STYLE + """
QWidget#acc { background: #111318; }
QWidget#col { background: transparent; }
QLabel { color: #e8e6e3; }
QLabel#big { font-size: 26px; font-weight: 800; color: #f5c542; }
QLabel#title { font-size: 13px; font-weight: 600; }
QPushButton { background: #2a2e37; color: #e8e6e3; border: 1px solid #383d48; border-radius: 7px; padding: 5px 10px; }
QPushButton:hover { background: #333845; }
QProgressBar { background: #1f2229; border: none; border-radius: 5px; height: 10px; text-align: right; color: transparent; }
QProgressBar::chunk { background: #f5c542; border-radius: 5px; }
QComboBox { background: #1f2229; color: #e8e6e3; border: 1px solid #383d48; border-radius: 6px; padding: 3px 6px; }
"""
MONTHS = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]
CHEST_LABELS = {"ordinaires": "Coffre ordinaire", "délicats": "Coffre délicat", "précieux": "Coffre précieux",
                "luxueux": "Coffre luxueux", "étranges": "Coffre étrange"}


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _left(sec: float) -> str:
    sec = int(sec)
    if sec <= 0:
        return "terminé"
    d, h = sec // 86400, sec % 86400 // 3600
    if d:
        return f"{d} j {h} h"
    m = sec % 3600 // 60
    return f"{h} h {m:02d}" if h else f"{m} min"


class IconLoader(QThread):
    got = pyqtSignal(object, object)   # (label, QImage)

    def __init__(self, folder, jobs):
        super().__init__()
        self.folder, self.jobs = folder, jobs

    def run(self):
        import hashlib
        self.folder.mkdir(parents=True, exist_ok=True)
        for lab, url in self.jobs:
            if not url:
                continue
            p = self.folder / (hashlib.md5(url.encode()).hexdigest() + ".png")
            try:
                if not p.exists():
                    r = requests.get(url, timeout=15)
                    if not r.ok:
                        continue
                    p.write_bytes(r.content)
                img = load_image(p)
                if not img.isNull():
                    self.got.emit(lab, img)
            except Exception:  # noqa: BLE001
                continue


def _column(title):
    col = QWidget()
    col.setObjectName("col")
    v = QVBoxLayout(col)
    v.setContentsMargins(0, 0, 0, 0)
    h = QLabel(title)
    h.setObjectName("h")
    v.addWidget(h)
    status = QLabel()
    status.setObjectName("muted")
    status.setWordWrap(True)
    v.addWidget(status)
    inner = QWidget()
    inner.setObjectName("col")
    body = QVBoxLayout(inner)
    body.setContentsMargins(0, 4, 6, 4)
    body.setSpacing(8)
    body.addStretch(1)
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sc.setWidget(inner)
    v.addWidget(sc, 1)
    return col, v, status, body


def _clear(lay):
    while lay.count() > 1:
        w = lay.takeAt(0).widget()
        if w:
            w.setParent(None)
            w.deleteLater()


def _add(lay, w):
    lay.insertWidget(lay.count() - 1, w)


class AccountPage(QWidget, JobOwner):
    def __init__(self, cfg: dict, cache_dir, overlay_counts=None, open_settings=None):
        super().__init__()
        self.setObjectName("acc")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.cfg, self.cache = cfg, cache_dir
        self.overlay_counts = overlay_counts or (lambda: {})
        self.open_settings = open_settings
        self._loaded = False

        top = QHBoxLayout()
        t = QLabel("Mon compte Genshin")
        t.setStyleSheet("font-size: 20px; font-weight: 700; color: #f5c542;")
        top.addWidget(t)
        top.addStretch(1)
        self.updated = QLabel()
        self.updated.setObjectName("muted")
        top.addWidget(self.updated)
        b = QPushButton("⟳ Actualiser")
        b.clicked.connect(self.load_all)
        top.addWidget(b)

        c1, _v1, self.exp_status, self.exp_box = _column("🗺 Exploration (officielle)")
        c2, v2, self.led_status, self.led_box = _column("📒 Journal du voyageur")
        self.month = QComboBox()
        self.month.currentIndexChanged.connect(self._month_changed)
        self.month.hide()
        v2.insertWidget(1, self.month)
        c3, _v3, self.cal_status, self.cal_box = _column("📅 Calendrier des événements")
        cols = QHBoxLayout()
        cols.setSpacing(18)
        cols.addWidget(c1, 1)
        cols.addWidget(c2, 1)
        cols.addWidget(c3, 1)

        self.missing = QWidget()
        ml = QVBoxLayout(self.missing)
        lab = QLabel("Relie ton compte HoYoLAB (cookies + UID) dans ⚙ Paramètres pour voir ton exploration, "
                     "ton journal du voyageur et le calendrier des événements.")
        lab.setWordWrap(True)
        lab.setStyleSheet("font-size: 14px; color: #e0a84a;")
        ml.addWidget(lab)
        b2 = QPushButton("Ouvrir les Paramètres")
        b2.clicked.connect(lambda: self.open_settings and self.open_settings())
        ml.addWidget(b2, 0, Qt.AlignmentFlag.AlignLeft)
        ml.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.addLayout(top)
        lay.addWidget(self.missing)
        lay.addLayout(cols, 1)
        credit = QLabel("Données : HoYoLAB (Battle Chronicle) — elles peuvent avoir quelques minutes de retard sur le jeu.")
        credit.setObjectName("muted")
        lay.addWidget(credit)
        self._cols = (c1, c2, c3)

    def _client(self):
        from .hoyonotes import HoyoClient
        return HoyoClient(self.cfg.get("hoyolab_cookies") or {}, self.cfg.get("uid", ""))

    def showEvent(self, e):
        super().showEvent(e)
        if not self._loaded:
            self._loaded = True
            self.load_all()

    def load_all(self):
        ok = bool(self.cfg.get("hoyolab_cookies")) and bool(self.cfg.get("uid"))
        self.missing.setVisible(not ok)
        for c in self._cols:
            c.setVisible(ok)
        if not ok:
            return
        self.updated.setText("Mis à jour " + time.strftime("à %H:%M"))
        for st in (self.exp_status, self.led_status, self.cal_status):
            st.setText("Chargement…")
        self._run(Job(lambda: self._client().index()), self._exploration, lambda m: self.exp_status.setText(f"⚠ {m}"))
        self.load_ledger()
        self._run(Job(lambda: self._client().calendar()), self._calendar, lambda m: self.cal_status.setText(f"⚠ {m}"))

    # ------------------------------------------------------------------ exploration
    def _exploration(self, d):
        _clear(self.exp_box)
        icons = []
        self.exp_status.setText(f"{len(d['regions'])} régions · {d['achievements']} succès · "
                                f"{d['waypoints']} téléporteurs · {d['days']} jours actifs")
        for r in d["regions"]:
            card = ClickFrame(clickable=False)
            g = QGridLayout(card)
            g.setContentsMargins(10, 8, 10, 8)
            ic = QLabel()
            ic.setFixedSize(36, 36)
            g.addWidget(ic, 0, 0, 2, 1)
            name = QLabel(r["name"])
            name.setObjectName("title")
            g.addWidget(name, 0, 1)
            pct = QLabel(f"<b>{r['pct']:.1f} %</b>" + (f"  ·  niv. {r['level']}" if r["level"] else ""))
            pct.setAlignment(Qt.AlignmentFlag.AlignRight)
            g.addWidget(pct, 0, 2)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(r["pct"] * 10))
            bar.setFixedHeight(9)
            g.addWidget(bar, 1, 1, 1, 2)
            g.setColumnStretch(1, 1)
            _add(self.exp_box, card)
            icons.append((ic, r["icon"]))
        # Comparaison : coffres ouverts selon HoYoLAB vs cochés dans l'overlay
        mine = self.overlay_counts() or {}
        card = ClickFrame(clickable=False)
        g = QGridLayout(card)
        g.setContentsMargins(12, 10, 12, 10)
        t = QLabel("Coffres ouverts — HoYoLAB / cochés dans l'overlay")
        t.setObjectName("title")
        g.addWidget(t, 0, 0, 1, 3)
        tot_o = tot_m = 0
        for i, (k, n) in enumerate(d["chests"].items(), start=1):
            m = mine.get(CHEST_LABELS[k], None)
            g.addWidget(QLabel(f"Coffres {k}"), i, 0)
            g.addWidget(QLabel(f"<b>{_fmt(n)}</b>"), i, 1)
            g.addWidget(QLabel("—" if m is None else f"{_fmt(m)}  <span style='color:#8b8f98'>({_gap(n, m)})</span>"), i, 2)
            tot_o += n
            tot_m += m or 0
        g.addWidget(QLabel("<b>Total</b>"), 7, 0)
        g.addWidget(QLabel(f"<b>{_fmt(tot_o)}</b>"), 7, 1)
        g.addWidget(QLabel(f"<b>{_fmt(tot_m)}</b>"), 7, 2)
        hint = QLabel("L'overlay ne connaît que ce que tu as coché (ou « Tout marquer fait »). Un grand écart = "
                      "des coffres ouverts sans les avoir cochés.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        g.addWidget(hint, 8, 0, 1, 3)
        _add(self.exp_box, card)
        if d["oculi"]:
            names = {"anemoculus": "Anémoculus", "geoculus": "Géoculus", "electroculus": "Électroculus",
                     "dendroculus": "Dendroculus", "hydroculus": "Hydroculus", "pyroculus": "Pyroculus",
                     "moonoculus": "Lunoculus"}
            oc = QLabel("Oculus : " + " · ".join(f"{names.get(k, k)} {v}" for k, v in d["oculi"].items()))
            oc.setWordWrap(True)
            oc.setObjectName("muted")
            _add(self.exp_box, oc)
        job = IconLoader(self.cache / "icons", icons)
        job.got.connect(lambda lab, img: _set_icon(lab, img, 36))
        self._run(job)

    # ------------------------------------------------------------------ journal du voyageur
    def load_ledger(self, month: int | None = None):
        self.led_status.setText("Chargement…")
        self._run(Job(lambda: self._client().ledger(month)), self._ledger, lambda m: self.led_status.setText(f"⚠ {m}"))

    def _month_changed(self, i):
        m = self.month.itemData(i)
        if m and m != getattr(self, "_shown_month", None):
            self.load_ledger(m)

    def _ledger(self, d):
        self._shown_month = d["month"]
        if d["months"]:
            self.month.blockSignals(True)
            self.month.clear()
            for m in d["months"]:
                self.month.addItem(MONTHS[m] if 0 < m < 13 else str(m), m)
            i = self.month.findData(d["month"])
            if i >= 0:
                self.month.setCurrentIndex(i)
            self.month.blockSignals(False)
            self.month.show()
        _clear(self.led_box)
        self.led_status.setText(f"Mois de {MONTHS[d['month']] if 0 < d['month'] < 13 else d['month']}")
        for title, val, last, rate, today in (("Primo-gemmes", d["primos"], d["last_primos"], d["primos_rate"],
                                               d["today_primos"]),
                                              ("Mora", d["mora"], d["last_mora"], d["mora_rate"], d["today_mora"])):
            card = ClickFrame(clickable=False)
            v = QVBoxLayout(card)
            v.setContentsMargins(14, 10, 14, 10)
            v.addWidget(QLabel(title))
            big = QLabel(_fmt(val))
            big.setObjectName("big")
            v.addWidget(big)
            col = "#74c2a8" if rate >= 0 else "#e07a5f"
            cmp_ = QLabel(f"<span style='color:{col}'>{'+' if rate >= 0 else ''}{rate} %</span> par rapport "
                          f"au mois dernier ({_fmt(last)})<br>Aujourd'hui : <b>{_fmt(today)}</b>")
            cmp_.setWordWrap(True)
            v.addWidget(cmp_)
            _add(self.led_box, card)
        if d["categories"]:
            card = ClickFrame(clickable=False)
            g = QGridLayout(card)
            g.setContentsMargins(14, 10, 14, 10)
            t = QLabel("D'où viennent tes primo-gemmes")
            t.setObjectName("title")
            g.addWidget(t, 0, 0, 1, 3)
            for i, c in enumerate(d["categories"], start=1):
                name = QLabel(f"{c['name']}  <span style='color:#8b8f98'>{_fmt(c['num'])} · {c['pct']} %</span>")
                name.setWordWrap(True)
                g.addWidget(name, 2 * i - 1, 0)
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setValue(c["pct"])
                bar.setFixedHeight(7)
                g.addWidget(bar, 2 * i, 0)
            _add(self.led_box, card)

    # ------------------------------------------------------------------ calendrier
    def _calendar(self, d):
        _clear(self.cal_box)
        now = time.time()
        live = [e for e in d["events"] if not e["finished"] and (not e["end"] or e["end"] > now)]
        done = [e for e in d["events"] if e not in live]
        self.cal_status.setText(f"{len(live)} événement(s) en cours · {len(done)} terminé(s)"
                                if d["events"] else "Aucun événement trouvé.")
        icons = []
        for b in d["banners"]:
            card = ClickFrame(clickable=False)
            v = QVBoxLayout(card)
            v.setContentsMargins(12, 8, 12, 8)
            head = QLabel(f"🌟 <b>{b['name']}</b>" + (f"  <span style='color:#8b8f98'>({b['version']})</span>"
                                                      if b["version"] else ""))
            head.setWordWrap(True)
            v.addWidget(head)
            row = QHBoxLayout()
            for it in b["items"][:6]:
                ic = QLabel()
                ic.setFixedSize(40, 40)
                ic.setToolTip(it["name"])
                row.addWidget(ic)
                icons.append((ic, it["icon"]))
            row.addStretch(1)
            v.addLayout(row)
            names = ", ".join(i["name"] for i in b["items"][:6])
            sub = QLabel(f"{names}<br><span style='color:#8b8f98'>se termine dans {_left(b['end'] - now)}</span>"
                         if b["end"] else names)
            sub.setWordWrap(True)
            sub.setStyleSheet("font-size: 11px;")
            v.addWidget(sub)
            _add(self.cal_box, card)
        for e in live + done:
            card = ClickFrame(clickable=False)
            v = QVBoxLayout(card)
            v.setContentsMargins(12, 8, 12, 8)
            left = e["end"] - now if e["end"] else None
            urgent = left is not None and 0 < left < 36 * 3600 and not e["finished"]
            if e["finished"]:
                tag = "<span style='color:#74c2a8'>✓ terminé</span>"
            elif left is None:
                tag = "<span style='color:#8b8f98'>permanent</span>"
            else:
                col = "#e07a5f" if urgent else "#a7abb3"
                tag = f"<span style='color:{col}'>⏳ {_left(left)}</span>"
            t = QLabel(f"<b>{e['name']}</b>   {tag}")
            t.setWordWrap(True)
            v.addWidget(t)
            if e["rewards"]:
                r = QLabel("Récompenses : " + ", ".join(f"{n} ×{k}" if k else n for n, k, _ in e["rewards"][:6]))
                r.setWordWrap(True)
                r.setStyleSheet("font-size: 11px; color: #a7abb3;")
                v.addWidget(r)
            if urgent:
                card.setStyleSheet("QFrame#card { border-color: #e07a5f; }")
            elif e["finished"]:
                card.setStyleSheet("QFrame#card { background: #15171c; }")
            _add(self.cal_box, card)
        job = IconLoader(self.cache / "icons", icons)
        job.got.connect(lambda lab, img: _set_icon(lab, img, 40))
        self._run(job)


def _gap(official, mine):
    d = official - mine
    return "identique" if d == 0 else (f"{d} non cochés" if d > 0 else f"{-d} de plus dans l'overlay")


def _set_icon(lab, img, size):
    try:
        lab.setPixmap(QPixmap.fromImage(img).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                                    Qt.TransformationMode.SmoothTransformation))
    except RuntimeError:   # étiquette détruite entre-temps
        pass
