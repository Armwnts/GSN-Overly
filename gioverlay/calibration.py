"""Écran de calibration : l'utilisateur entoure la mini-map d'un carré."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from . import macos

HELP = ("Trace un carré qui entoure exactement le cercle de la mini-map  •  "
        "Entrée : valider  •  Échap : annuler  •  flèches : ajuster de 1 pt")


class CalibrationWindow(QWidget):
    done = pyqtSignal(object)  # {"x","y","size"} en points globaux, ou None si annulé

    def __init__(self, previous: dict | None = None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.screen_ = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = self.screen_.geometry()
        self.setGeometry(geo)
        # Capture figée de l'écran : on voit le jeu même si la fenêtre le recouvre.
        self.shot = self.screen_.grabWindow(0)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.start: QPoint | None = None
        self.rect_ = QRect()
        if previous:
            r = QRect(int(previous["x"]) - geo.x(), int(previous["y"]) - geo.y(),
                      int(previous["size"]), int(previous["size"]))
            if geo.translated(-geo.topLeft()).contains(r):
                self.rect_ = r
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def showEvent(self, e):
        super().showEvent(e)
        macos.make_floating_window(self)
        self.activateWindow()
        self.setFocus()

    def mousePressEvent(self, e):
        self.start = e.position().toPoint()
        self.rect_ = QRect(self.start, self.start)
        self.update()

    def mouseMoveEvent(self, e):
        if self.start is None:
            return
        cur = e.position().toPoint()
        side = max(abs(cur.x() - self.start.x()), abs(cur.y() - self.start.y()))
        sx = 1 if cur.x() >= self.start.x() else -1
        sy = 1 if cur.y() >= self.start.y() else -1
        self.rect_ = QRect(self.start, QPoint(self.start.x() + sx * side, self.start.y() + sy * side)).normalized()
        self.update()

    def mouseReleaseEvent(self, e):
        self.start = None

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.done.emit(None)
            self.close()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.rect_.width() >= 40:
                g = self.screen_.geometry()
                self.done.emit({"x": self.rect_.x() + g.x(), "y": self.rect_.y() + g.y(),
                                "size": self.rect_.width()})
                self.close()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            grow = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            dx = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}.get(k, 0)
            dy = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1}.get(k, 0)
            if grow:  # Maj + flèches : agrandir / réduire
                d = dx or -dy
                self.rect_.setWidth(self.rect_.width() + d)
                self.rect_.setHeight(self.rect_.height() + d)
            else:
                self.rect_.translate(dx, dy)
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(self.rect(), self.shot)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if not self.rect_.isEmpty():
            p.drawPixmap(self.rect_, self.shot, self._src_rect(self.rect_))
            p.setPen(QPen(QColor("#3ddc84"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self.rect_)
            p.setPen(QPen(QColor("#f5c542"), 1, Qt.PenStyle.DashLine))
            p.drawEllipse(QPointF(self.rect_.center()), self.rect_.width() / 2, self.rect_.height() / 2)
        p.setPen(QColor("white"))
        f = QFont()
        f.setPointSize(15)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect().adjusted(0, 0, 0, -40), Qt.AlignmentFlag.AlignHCenter
                   | Qt.AlignmentFlag.AlignBottom, HELP)
        p.end()

    def _src_rect(self, r: QRect) -> QRect:
        k = self.shot.devicePixelRatio()
        return QRect(int(r.x() * k), int(r.y() * k), int(r.width() * k), int(r.height() * k))
