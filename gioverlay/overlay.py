"""Fenêtre transparente dessinée par-dessus la mini-map du jeu."""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from . import macos, winutil

MARGIN = 6  # px autour de la mini-map (les icônes au bord ne sont pas coupées)


@dataclass
class Marker:
    x: float            # position dans la fenêtre overlay (points)
    y: float
    color: str
    label_id: int
    layered: bool = False
    edge: bool = False  # point hors de la mini-map : indicateur de direction au bord
    pid: int | None = None  # identifiant du point (carte plein écran)


class Overlay(QWidget):
    def __init__(self, icon_path_for, icon_size: int = 16):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # Sur macOS, Qt cache les fenêtres « Tool » quand l'app n'est pas au premier plan.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self._icon_path_for = icon_path_for
        self._icons: dict[int, QPixmap | None] = {}
        self.icon_size = icon_size
        self.markers: list[Marker] = []
        self.state = "idle"  # idle | ok | lost
        self.size_pt = 0.0
        self._mac_done = False

    def showEvent(self, e):
        super().showEvent(e)
        if not self._mac_done:
            macos.make_overlay_window(self)
            winutil.exclude_from_capture(self)
            self._mac_done = True

    def set_region(self, x: float, y: float, size: float) -> None:
        self.size_pt = size
        self.setGeometry(int(round(x - MARGIN)), int(round(y - MARGIN)),
                         int(round(size + 2 * MARGIN)), int(round(size + 2 * MARGIN)))

    def set_markers(self, markers: list[Marker], state: str) -> None:
        self.markers = markers
        self.state = state
        self.update()

    def _icon(self, label_id: int) -> QPixmap | None:
        if label_id not in self._icons:
            p = self._icon_path_for(label_id)
            pm = QPixmap(str(p)) if p.exists() else QPixmap()
            self._icons[label_id] = None if pm.isNull() else pm.scaled(
                self.icon_size, self.icon_size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        return self._icons[label_id]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        half = self.icon_size / 2
        for m in self.markers:
            col = QColor(m.color)
            if m.edge:
                p.setPen(QPen(QColor(0, 0, 0, 160), 1))
                col.setAlpha(210)
                p.setBrush(col)
                p.drawEllipse(QPointF(m.x, m.y), 3.2, 3.2)
                continue
            # halo coloré = catégorie ; pointillés = étage / souterrain
            pen = QPen(col, 2)
            if m.layered:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            fill = QColor(0, 0, 0, 110)
            p.setBrush(fill)
            p.drawEllipse(QPointF(m.x, m.y), half + 2, half + 2)
            icon = self._icon(m.label_id)
            if icon is not None:
                p.setOpacity(0.65 if m.layered else 1.0)
                p.drawPixmap(QRectF(m.x - half, m.y - half, self.icon_size, self.icon_size),
                             icon, QRectF(icon.rect()))
                p.setOpacity(1.0)
            else:
                p.setBrush(col)
                p.drawEllipse(QPointF(m.x, m.y), 4, 4)
        # petit voyant d'état en haut à droite de la mini-map
        dot = {"ok": QColor("#3ddc84"), "lost": QColor("#ff9f43"), "idle": QColor("#888888")}[self.state]
        p.setPen(QPen(QColor(0, 0, 0, 180), 1))
        p.setBrush(dot)
        p.drawEllipse(QPointF(MARGIN + self.size_pt * 0.9, MARGIN + self.size_pt * 0.1), 4, 4)
        p.end()


class MapOverlay(QWidget):
    """Couvre tout l'écran quand la carte du jeu est ouverte : grosses icônes
    sur les coffres et défis restants. Transparent aux clics."""

    def __init__(self, icon_path_for):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        for a in (Qt.WidgetAttribute.WA_TranslucentBackground, Qt.WidgetAttribute.WA_ShowWithoutActivating,
                  Qt.WidgetAttribute.WA_TransparentForMouseEvents, Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow):
            self.setAttribute(a)
        self._icon_path_for = icon_path_for
        self._icons: dict[tuple[int, int], QPixmap | None] = {}
        self.markers: list[Marker] = []
        self.icon_size = 26
        self._mac_done = False

    def showEvent(self, e):
        super().showEvent(e)
        if not self._mac_done:
            macos.make_overlay_window(self)
            winutil.exclude_from_capture(self)
            self._mac_done = True

    def set_screen(self, rect):
        x, y, w, h = rect
        if (self.x(), self.y(), self.width(), self.height()) != (x, y, w, h):
            self.setGeometry(int(x), int(y), int(w), int(h))

    def set_markers(self, markers: list[Marker], icon_size: int):
        self.markers = markers
        self.icon_size = icon_size
        self.update()

    def _icon(self, label_id: int, size: int) -> QPixmap | None:
        key = (label_id, size)
        if key not in self._icons:
            p = self._icon_path_for(label_id)
            pm = QPixmap(str(p)) if p.exists() else QPixmap()
            self._icons[key] = None if pm.isNull() else pm.scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return self._icons[key]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sz = self.icon_size
        half = sz / 2
        for m in self.markers:
            col = QColor(m.color)
            pen = QPen(col, 3)
            if m.layered:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(QColor(10, 12, 20, 170))
            p.drawEllipse(QPointF(m.x, m.y), half + 4, half + 4)
            icon = self._icon(m.label_id, sz)
            if icon is not None:
                p.setOpacity(0.7 if m.layered else 1.0)
                p.drawPixmap(QRectF(m.x - half, m.y - half, sz, sz), icon, QRectF(icon.rect()))
                p.setOpacity(1.0)
            else:
                p.setBrush(col)
                p.drawEllipse(QPointF(m.x, m.y), half * 0.5, half * 0.5)
        p.end()


class QuestCard(QWidget):
    """Carte d'aide de quête, en bas à gauche de l'écran, à côté du bouton « Entrée »
    (chat) de Genshin. Transparente aux clics : elle ne gêne pas le jeu."""

    STYLE = """
    QFrame#card { background: rgba(14, 17, 26, 215); border: 1px solid rgba(236, 196, 92, 150);
                  border-radius: 12px; }
    QLabel { color: #ece8e1; background: transparent; font-size: 13px; }
    QLabel#q { color: #f5c542; font-size: 15px; font-weight: 700; }
    QLabel#step { color: #b9c3d6; font-size: 12px; }
    QLabel#tip { color: #9be06b; font-size: 12px; }
    QLabel#foot { color: #7d8597; font-size: 10px; }
    """

    def __init__(self):
        from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        for a in (Qt.WidgetAttribute.WA_TranslucentBackground, Qt.WidgetAttribute.WA_ShowWithoutActivating,
                  Qt.WidgetAttribute.WA_TransparentForMouseEvents, Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow):
            self.setAttribute(a)
        self.setStyleSheet(self.STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame()
        self.card.setObjectName("card")
        outer.addWidget(self.card)
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(16, 12, 16, 10)
        lay.setSpacing(6)
        self.q, self.step, self.body, self.tip, self.foot = (QLabel() for _ in range(5))
        for w, name in ((self.q, "q"), (self.step, "step"), (self.body, "body"), (self.tip, "tip"), (self.foot, "foot")):
            w.setObjectName(name)
            w.setWordWrap(True)
            w.setTextFormat(Qt.TextFormat.PlainText)
            lay.addWidget(w)
        self.anchor = None   # (gauche, bas, largeur) en coordonnées écran
        self._mac_done = False

    def showEvent(self, e):
        super().showEvent(e)
        if not self._mac_done:
            macos.make_overlay_window(self)
            winutil.exclude_from_capture(self)
            self._mac_done = True

    def place(self, screen_rect, cfg: dict):
        x, y, w, h = screen_rect
        width = max(360, int(w * cfg.get("width", 0.30)))
        self.anchor = (int(x + w * cfg.get("left", 0.055)), int(y + h * cfg.get("bottom", 0.965)), width)
        self._relayout()

    def _relayout(self):
        if not self.anchor:
            return
        left, bottom, width = self.anchor
        self.setFixedWidth(width)
        self.adjustSize()
        self.move(left, bottom - self.height())

    def _set(self, label, text):
        label.setText(text)
        label.setVisible(bool(text))

    def show_message(self, title: str, text: str = "", foot: str = ""):
        self._set(self.q, title)
        self._set(self.step, "")
        self._set(self.body, text)
        self._set(self.tip, "")
        self._set(self.foot, foot)
        self._relayout()
        self.show()
        self.raise_()

    def show_answer(self, ans, close_hint: str):
        self._set(self.q, ans.quest or "Quête")
        self._set(self.step, f"Étape : {ans.step}" if ans.step else "")
        self._set(self.body, "\n".join(ans.lines))
        self._set(self.tip, f"💡 {ans.tip}" if ans.tip else "")
        src = f"Sources : {', '.join(ans.sources)}  •  " if ans.sources else ""
        self._set(self.foot, f"{src}{close_hint}")
        self._relayout()
        self.show()
        self.raise_()
