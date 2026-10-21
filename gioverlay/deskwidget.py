"""Mini-widget « je joue pas, mais » : une petite carte posée sur le bureau (comme les widgets de
macOS) avec la résine, l'argent du domaine, les commissions/expéditions, la connexion du jour et les
événements qui se terminent bientôt.

- glisser pour le déplacer (la position est retenue) ;
- clic : affiche le panneau de l'overlay ;
- clic droit : menu (actualiser, ouvrir la carte, masquer le widget, quitter…).
"""
from __future__ import annotations

import logging
import sys
import time

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

log = logging.getLogger("gio.widget")
W = 270


def _desktop_level(widget, on_top: bool):
    """macOS : au niveau des widgets du bureau (sous les fenêtres), visible sur tous les bureaux."""
    if sys.platform != "darwin":
        return
    try:
        import objc
        from AppKit import (NSFloatingWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
                            NSWindowCollectionBehaviorStationary)
        view = objc.objc_object(c_void_p=int(widget.winId()))
        win = view.window()
        if on_top:
            win.setLevel_(NSFloatingWindowLevel)
        else:
            from Quartz import CGWindowLevelForKey, kCGDesktopIconWindowLevelKey
            win.setLevel_(CGWindowLevelForKey(kCGDesktopIconWindowLevelKey) + 1)
        win.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary)
        win.setHidesOnDeactivate_(False)
        win.setHasShadow_(True)
    except Exception as e:  # noqa: BLE001
        log.warning("Niveau du widget : %s", e)


class DeskWidget(QWidget):
    def __init__(self, ctrl):
        on_top = bool(ctrl.cfg.get("desk_widget_ontop", False))
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        flags |= Qt.WindowType.WindowStaysOnTopHint if on_top else Qt.WindowType.WindowStaysOnBottomHint
        super().__init__(None, flags)
        self.ctrl = ctrl
        self.on_top = on_top
        from .menubar import MenuBarWidget
        self.src = MenuBarWidget(ctrl)          # même contenu que le menu de la barre des menus
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Genshin Overlay — widget")
        self.setFixedWidth(W)
        self._drag = None
        self._moved = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.moon = QLabel("☾")
        self.moon.setStyleSheet("color:#f5c542; font-size:30px; font-weight:800;")
        self.resin = QLabel("—")
        self.resin.setTextFormat(Qt.TextFormat.RichText)
        self.resin.setStyleSheet("color:#f4f1ea; font-size:26px; font-weight:800;")
        self.full = QLabel("")
        self.full.setStyleSheet("color:#a7abb3; font-size:12px;")
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(self.resin)
        col.addWidget(self.full)
        top.addWidget(self.moon)
        top.addLayout(col, 1)
        lay.addLayout(top)
        self.bar = QFrame()
        self.bar.setFixedHeight(5)
        self.bar_fill = QFrame(self.bar)
        self.bar.setStyleSheet("QFrame { background: rgba(255,255,255,0.12); border-radius: 2px; }")
        lay.addWidget(self.bar)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setStyleSheet("color:#d8d6d0; font-size:12px;")
        lay.addWidget(self.body)
        self.timer = QTimer(self, interval=60_000, timeout=self.refresh)

    # ------------------------------------------------------------------ dessin
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 20, 20)
        p.fillPath(path, QColor(18, 20, 26, 232))
        p.setPen(QColor(255, 255, 255, 30))
        p.drawPath(path)

    # ------------------------------------------------------------------ contenu
    def refresh(self):
        try:
            self._refresh()
        except Exception as e:  # noqa: BLE001
            log.warning("Widget : %s", e)

    def _refresh(self):
        from .hoyonotes import duration, resin_now
        c = self.ctrl
        n = c.notes
        accent = c.cfg.get("accent_color") or "#F5C542"
        self.moon.setStyleSheet(f"color:{accent}; font-size:30px; font-weight:800;")
        if not c.cfg.get("hoyolab_cookies") or not c.cfg.get("uid"):
            self.resin.setText("Résine")
            self.full.setText("Relie ton compte HoYoLAB")
            self.body.setText("<span style='color:#a7abb3'>Paramètres → HoYoLAB : colle tes cookies "
                              "pour voir ta résine ici.</span>")
            self._set_bar(0)
            self.layout().activate()
            self.setFixedHeight(self.layout().sizeHint().height())
            return
        elif not n:
            self.resin.setText("…")
            self.full.setText("chargement des notes HoYoLAB")
            self.body.setText("")
            self._set_bar(0)
        else:
            now = time.time()
            r, mx = resin_now(n), n["max_resin"]
            self.resin.setText(f"{r}<span style='color:#8b8f98; font-size:15px'> / {mx}</span>")
            self.full.setText("pleine !" if r >= mx else f"pleine dans {duration(n['resin_full_at'] - now)}")
            self.full.setStyleSheet(f"color:{'#e07a5f' if r >= mx else '#a7abb3'}; font-size:12px;")
            self._set_bar(r / max(1, mx), "#e07a5f" if r >= mx else accent)
            self.body.setText("<br>".join(_esc(l) for l in self._rows(n, now)))
        self.layout().activate()
        self.setFixedHeight(self.layout().sizeHint().height())

    def _rows(self, n, now):
        from .menubar import _fmt_left
        c = self.ctrl
        rows = []
        if n.get("max_coins"):
            rows.append(f"🏠 {n['coins']}/{n['max_coins']}" + (" · plein !" if now >= n["coins_full_at"] else
                                                              f" · plein dans {_fmt_left(n['coins_full_at'] - now)}"))
        exp = f"{n['exp_done']} prêtes" if n["exp_done"] else f"{n['exp_total']} en cours"
        rows.append(f"📋 {n['tasks_done']}/{n['tasks_total']}  ·  🧭 {exp}")
        if n.get("transformer_ready"):
            rows.append("🔧 Transformateur prêt")
        today = time.strftime("%Y-%m-%d", time.gmtime(now + 8 * 3600))
        if c.cfg.get("last_checkin") == today:
            rows.append("🎁 Connexion du jour ✓")
        elif getattr(c, "checkin_text", None):
            rows.append("🎁 Connexion du jour : échec")
        cal = getattr(c, "calendar", None) or {}
        evs = sorted((e for e in cal.get("events", []) if not e.get("finished") and e.get("end")
                      and 0 < e["end"] - now < 7 * 86400), key=lambda e: e["end"])
        for e in evs[:2]:
            name = e["name"] if len(e["name"]) <= 24 else e["name"][:23] + "…"
            rows.append(f"⏳ {name} · {_fmt_left(e['end'] - now)}")
        return rows

    def _set_bar(self, frac, color="#f5c542"):
        w = max(0, int((W - 32) * min(1.0, frac)))
        self.bar_fill.setGeometry(0, 0, w, 5)
        self.bar_fill.setStyleSheet(f"background:{color}; border-radius:2px;")

    # ------------------------------------------------------------------ affichage
    def start(self):
        pos = self.ctrl.cfg.get("desk_widget_pos")
        self.refresh()
        if pos:
            self.move(int(pos[0]), int(pos[1]))
        else:
            from PyQt6.QtGui import QGuiApplication
            g = QGuiApplication.primaryScreen().availableGeometry()
            self.move(g.right() - W - 24, g.top() + 24)
        self.show()
        _desktop_level(self, self.on_top)
        self.timer.start()
        log.info("Mini-widget affiché en %s,%s", self.x(), self.y())

    def stop(self):
        self.timer.stop()
        self.hide()
        self.deleteLater()

    @property
    def active(self) -> bool:
        return self.isVisible()

    # ------------------------------------------------------------------ souris
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            p = e.globalPosition().toPoint() - self._drag
            if (p - self.pos()).manhattanLength() > 3:
                self._moved = True
            self.move(p)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._drag is None:
            return
        self._drag = None
        if self._moved:
            self.ctrl.cfg["desk_widget_pos"] = [self.x(), self.y()]
            from .config import save_config
            save_config(self.ctrl.cfg)
        else:
            self.ctrl.show_panel()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("Afficher le panneau").triggered.connect(self.ctrl.show_panel)
        m.addAction("Ouvrir la carte et le compte…").triggered.connect(self.ctrl.open_map_window)
        m.addAction("⟳ Actualiser").triggered.connect(
            lambda: (self.ctrl.refresh_notes(force=True), self.ctrl.check_events()))
        m.addSeparator()
        m.addAction("Masquer le widget").triggered.connect(lambda: self.ctrl.set_desk_widget(False))
        m.addAction("Quitter Genshin Overlay").triggered.connect(self.ctrl.app.quit)
        m.exec(e.globalPos())


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
