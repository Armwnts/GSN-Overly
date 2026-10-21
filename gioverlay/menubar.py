"""Mode « je joue pas, mais » : un petit widget dans la barre des menus du Mac (zone de notification
sous Windows) avec la résine, l'argent du domaine, les commissions/expéditions et les événements qui
se terminent bientôt — sans ouvrir l'application.

Une icône QSystemTrayIcon (barre des menus du Mac, zone de notification de Windows) dessinée avec
le croissant de lune et la résine, et un menu.
"""
from __future__ import annotations

import logging
import sys
import time

from PyQt6.QtCore import QObject, QTimer

log = logging.getLogger("gio.barre")

def _fmt_left(sec: float) -> str:
    sec = max(0, int(sec))
    d, h, m = sec // 86400, sec % 86400 // 3600, sec % 3600 // 60
    if d:
        return f"{d} j {h} h"
    return f"{h} h {m:02d}" if h else f"{m} min"


class MenuBarWidget(QObject):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._tray = None        # QSystemTrayIcon
        self.timer = QTimer(self, interval=60_000, timeout=self.update)

    # ------------------------------------------------------------------ contenu
    def _title(self) -> str:
        from .hoyonotes import resin_now
        n = self.ctrl.notes
        if not n:
            return "🌙"
        r, mx = resin_now(n), n["max_resin"]
        return f"🌙 {r}" + (" !" if r >= mx else "")

    def _lines(self) -> list[str]:
        """Lignes d'information (non cliquables)."""
        from .hoyonotes import duration, resin_now
        c = self.ctrl
        n = c.notes
        out = []
        if not c.cfg.get("hoyolab_cookies") or not c.cfg.get("uid"):
            return ["Relie ton compte HoYoLAB (Paramètres) pour voir ta résine ici."]
        if not n:
            return ["Notes HoYoLAB pas encore chargées…"]
        now = time.time()
        r, mx = resin_now(n), n["max_resin"]
        out.append(f"🌙 Résine {r}/{mx} · " + ("pleine !" if r >= mx else f"pleine dans {duration(n['resin_full_at'] - now)}"))
        if n.get("max_coins"):
            if now >= n["coins_full_at"]:
                out.append(f"🏠 Argent du domaine plein ({n['max_coins']})")
            else:
                out.append(f"🏠 Argent du domaine {n['coins']}/{n['max_coins']} · plein dans "
                           f"{_fmt_left(n['coins_full_at'] - now)}")
        tasks = f"{n['tasks_done']}/{n['tasks_total']}" + (" ✓" if n.get("tasks_bonus") else "")
        exp = f"{n['exp_done']}/{n['exp_total']} prêtes" if n["exp_done"] else f"{n['exp_total']} en cours"
        out.append(f"📋 Commissions {tasks} · 🧭 Expéditions {exp}")
        if n.get("transformer_ready"):
            out.append("🔧 Transformateur paramétrique prêt")
        ci = getattr(c, "checkin_text", None)
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
        if c.cfg.get("last_checkin") == today:
            ci = "🎁 Connexion HoYoLAB du jour : récupérée ✓"
        elif not c.cfg.get("auto_checkin", True):
            ci = "🎁 Connexion HoYoLAB du jour : pas encore faite (automatique désactivée)"
        if ci:
            out.append(ci)
        return out

    def _events(self) -> list[str]:
        cal = getattr(self.ctrl, "calendar", None)
        if not cal:
            return []
        now = time.time()
        evs = [e for e in cal.get("events", []) if not e.get("finished") and e.get("end") and e["end"] > now]
        evs.sort(key=lambda e: e["end"])
        return [f"⏳ {e['name'][:42]} — {_fmt_left(e['end'] - now)}" for e in evs[:5] if e["end"] - now < 7 * 86400]

    def _actions(self):
        c = self.ctrl
        panel_txt = "Masquer le panneau" if c.panel.isVisible() else "Afficher le panneau"
        return [(panel_txt, self._toggle_panel),
                ("Ouvrir la carte et le compte…", c.open_map_window),
                ("⟳ Actualiser", lambda: (c.refresh_notes(force=True), c.check_events())),
                (None, None),
                ("Quitter Genshin Overlay", c.app.quit)]

    def _toggle_panel(self):
        p = self.ctrl.panel
        if p.isVisible():
            p.hide()
        else:
            p.show()
            p.raise_()
        self.update()

    # ------------------------------------------------------------------ affichage
    @property
    def active(self) -> bool:
        return self._tray is not None and self._tray.isVisible()

    def start(self):
        from PyQt6.QtWidgets import QSystemTrayIcon
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                log.warning("Barre des menus / zone de notification indisponible sur ce système")
                return
            self._tray = QSystemTrayIcon(self._icon("🌙"))
            self._tray.activated.connect(lambda reason: self.update())
            self._tray.show()
        except Exception as e:  # noqa: BLE001
            log.warning("Barre des menus indisponible : %s", e)
            return
        self.update()
        self.timer.start()
        log.info("Widget de la barre des menus affiché (%s)", "visible" if self._tray.isVisible() else "caché")

    def stop(self):
        self.timer.stop()
        if self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()
        self._tray = None

    def _icon(self, title: str):
        """Croissant de lune + résine, en noir sur transparent : macOS l'adapte au mode clair/sombre."""
        from PyQt6.QtCore import QRectF, Qt
        from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
        text = title.replace("🌙", "").strip()
        k = 2                                  # dessin en double résolution (écrans Retina)
        h = 22 * k
        font = QFont()
        font.setPixelSize(14 * k)
        font.setBold(True)
        from PyQt6.QtGui import QFontMetrics
        tw = QFontMetrics(font).horizontalAdvance(text) if text else 0
        w = 20 * k + (tw + 3 * k if text else 0)
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0))
        moon = QPainterPath()
        moon.addEllipse(QRectF(2 * k, 3 * k, 16 * k, 16 * k))
        cut = QPainterPath()
        cut.addEllipse(QRectF(7 * k, 1 * k, 15 * k, 15 * k))
        p.drawPath(moon.subtracted(cut))
        if text:
            p.setPen(QColor(0, 0, 0))
            p.setFont(font)
            p.drawText(QRectF(20 * k, 0, tw + 3 * k, h), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        p.end()
        pm.setDevicePixelRatio(k)
        icon = QIcon(pm)
        icon.setIsMask(True)
        return icon

    def update(self):
        if self._tray is None:
            return
        try:
            info = self._lines()
            events = self._events()
            title = self._title()
            if title != getattr(self, "_last_title", None):
                self._last_title = title
                self._tray.setIcon(self._icon(title))
            self._update_menu(info, events)
        except Exception as e:  # noqa: BLE001
            log.warning("Barre des menus : %s", e)

    def _update_menu(self, info, events):
        from PyQt6.QtWidgets import QMenu
        self._tray.setToolTip("Genshin Overlay\n" + "\n".join(info[:3]))
        menu = QMenu()
        for line in info:
            menu.addAction(line).setEnabled(False)
        if events:
            menu.addSeparator()
            menu.addAction("Événements qui se terminent :").setEnabled(False)
            for line in events:
                menu.addAction(line).setEnabled(False)
        menu.addSeparator()
        for text, cb in self._actions():
            if text is None:
                menu.addSeparator()
            else:
                menu.addAction(text).triggered.connect(cb)
        old = getattr(self, "_menu", None)
        self._menu = menu            # garder une référence
        self._tray.setContextMenu(menu)
        if old is not None:
            old.deleteLater()
