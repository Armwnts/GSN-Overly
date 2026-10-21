"""Petits outils d'interface partagés par les pages Builds et News."""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QFrame

COMMON_STYLE = """
QLabel#h { font-size: 16px; font-weight: 600; color: #f5c542; }
QLabel#muted { color: #8b8f98; font-size: 11px; }
QSplitter::handle { background: #0f1115; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #383d48; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QPushButton#pill { background: #1f2229; border: 1px solid #383d48; border-radius: 13px; padding: 5px 12px;
                   color: #a7abb3; font-weight: 600; }
QPushButton#pill:checked { background: #f5c542; color: #16181d; border-color: #f5c542; }
QFrame#card { background: #1b1e24; border: 1px solid #262a33; border-radius: 10px; }
QFrame#card:hover { border-color: #4a5060; }
QFrame#card QLabel { background: transparent; }
"""


class Job(QThread):
    """Exécute fn(*args) hors de l'interface ; résultat via `ok` ou `fail` (message en français)."""
    ok = pyqtSignal(object)
    fail = pyqtSignal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.ok.emit(self.fn(*self.args))
        except Exception as e:  # noqa: BLE001 — on affiche l'erreur plutôt que de planter
            import logging
            name = getattr(self.fn, "__qualname__", getattr(self.fn, "__name__", "tâche"))
            logging.getLogger("gio.taches").warning("%s : %s", name, e,
                                                     exc_info=not isinstance(e, (OSError, RuntimeError)))
            self.fail.emit(str(e) or type(e).__name__)


class JobOwner:
    """Garde une référence sur les tâches en cours (sinon Qt les détruit trop tôt)."""

    def _run(self, job: QThread, ok=None, fail=None):
        if not hasattr(self, "_jobs"):
            self._jobs = set()
        self._jobs.add(job)
        if ok and hasattr(job, "ok"):
            job.ok.connect(ok)
        if fail and hasattr(job, "fail"):
            job.fail.connect(fail)
        job.finished.connect(lambda j=job: self._jobs.discard(j))
        job.start()
        return job


class ClickFrame(QFrame):
    clicked = pyqtSignal()

    def __init__(self, clickable=True):
        super().__init__()
        self.setObjectName("card")
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clickable = clickable

    def mouseReleaseEvent(self, e):
        if self._clickable and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


def load_image(path: Path) -> QImage:
    img = QImage(str(path))
    if img.isNull():  # format non pris en charge par Qt : on passe par OpenCV
        import cv2
        a = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if a is not None:
            if a.ndim == 2:
                a = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
            if a.shape[2] == 4:
                a = cv2.cvtColor(a, cv2.COLOR_BGRA2RGBA)
                fmt = QImage.Format.Format_RGBA8888
            else:
                a = cv2.cvtColor(a, cv2.COLOR_BGR2RGB)
                fmt = QImage.Format.Format_RGB888
            img = QImage(a.data, a.shape[1], a.shape[0], a.strides[0], fmt).copy()
    return img


def when(ts: float) -> str:
    """« il y a 3 h », « hier », « 12/09/2026 » ; pour une date future : « dans 5 j », « le 12/09/2026 »."""
    if not ts:
        return ""
    d = time.time() - ts
    if d < 0:
        days = -d / 86400
        return f"dans {int(days) + 1} j ({time.strftime('%d/%m/%Y', time.localtime(ts))})" if days < 60 \
            else time.strftime("le %d/%m/%Y", time.localtime(ts))
    if d < 3600:
        return f"il y a {max(1, int(d // 60))} min"
    if d < 86400:
        return f"il y a {int(d // 3600)} h"
    if d < 2 * 86400:
        return "hier"
    if d < 7 * 86400:
        return f"il y a {int(d // 86400)} j"
    return time.strftime("%d/%m/%Y", time.localtime(ts))
