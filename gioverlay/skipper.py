"""Mode « Passer les dialogues » : repère un dialogue à l'écran, appuie sur F à la place du joueur,
et affiche « Dialogue terminé » quand l'interface normale du jeu (Paimon + mini-map) est revenue."""
from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

import logging

from . import diag, dialogue, macos

log = logging.getLogger("gio.dialogues")

DETECT_MS = 250        # analyse de l'écran 4 fois par seconde
PRESS_MS = 200         # F toutes les 0,2 s pendant un dialogue
END_AFTER = 3.0        # sans mini-map calibrée : plus de boutons de dialogue depuis 3 s = terminé
HUD_STABLE = 0.75      # l'interface normale doit être revenue depuis 0,75 s
KEEP_PRESSING = 15.0   # entre deux répliques (fondu, cinématique…) on continue d'appuyer jusqu'à 15 s
GIVE_UP = 90.0         # ni dialogue ni interface depuis 90 s : on abandonne sans message
TOAST_MS = 10_000


class Toast(QWidget):
    """Message vert au-dessus du jeu, qui disparaît tout seul."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("QLabel { color: #4ade80; background: rgba(10, 20, 14, 200); "
                                 "border: 2px solid #22c55e; border-radius: 14px; padding: 12px 26px; "
                                 "font-size: 22px; font-weight: 800; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label)
        self._hide_timer = QTimer(self, singleShot=True, timeout=self._fade)
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.finished.connect(self._faded)

    def show_message(self, text: str, near=None, ms: int = TOAST_MS):
        self.label.setText(text)
        self.adjustSize()
        scr = QGuiApplication.screenAt(near) if near is not None else None
        g = (scr or QGuiApplication.primaryScreen()).geometry()
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + int(g.height() * 0.14))
        self._anim.stop()
        self.setWindowOpacity(1.0)
        self.show()
        macos.make_overlay_window(self, click_through=True)
        self._hide_timer.start(ms)

    def _fade(self):
        self._anim.stop()
        self._anim.setDuration(800)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _faded(self):
        if self.windowOpacity() < 0.05:
            self.hide()


class DialogueSkipper(QObject):
    status = pyqtSignal(str)
    finished = pyqtSignal(int)     # nombre d'appuis sur F pendant le dialogue

    def __init__(self, capture, screen_point, hud_frame=None, game_pid_fn=None):
        super().__init__()
        self.game_pid_fn = game_pid_fn            # () -> pid de l'application du jeu (ou None)
        self._last_doubt_log = 0.0
        self.capture = capture
        self.screen_point = screen_point          # () -> (x, y) d'un point sur l'écran du jeu
        self.hud_frame = hud_frame                # () -> image de la mini-map calibrée, ou None
        self.detect_timer = QTimer(self, interval=DETECT_MS, timeout=self._detect)
        self.key_timer = QTimer(self, interval=PRESS_MS, timeout=self._press)
        self.toast = Toast()
        self.enabled = False
        self.active = False
        self.started = self.last_seen = 0.0
        self.presses = 0
        self.game_pid = None
        self.last_score = 0.0
        self.errors = 0
        self.hud_since = None

    def set_enabled(self, on: bool):
        self.enabled = on
        if on:
            self.detect_timer.start()
        else:
            self.detect_timer.stop()
            self.key_timer.stop()
            self.active = False

    def _game_front(self) -> tuple[bool, int]:
        """Le jeu est-il au premier plan ? Le « jeu » est l'application qui était devant quand la
        mini-map a été vue (fourni par l'overlay). Sans cette info, on refuse par prudence."""
        pid, _ = dialogue.frontmost_app()
        gp = self.game_pid_fn() if self.game_pid_fn else None
        return (gp is not None and pid == gp), pid

    def _detect(self):
        x, y = self.screen_point()
        front, pid = self._game_front()
        if not front:
            # Autre application devant (navigateur, réglages…) : on ne capture rien et on n'appuie sur rien.
            return
        hud = self._hud()
        try:
            rect = dialogue.game_rect(pid)
            if rect is not None:   # seulement l'intérieur de la fenêtre du jeu
                img, h_px = self.capture.grab_game_corner(rect, dialogue.region_of_screen)
            else:
                img, h_px = self.capture.grab_corner_at(x, y, dialogue.region_of_screen)
            ok, self.last_score = dialogue.detect(img, h_px, positional=rect is not None)
        except Exception as e:  # noqa: BLE001 — capture impossible : on réessaie au tour suivant
            self.errors += 1
            if self.errors in (1, 5):
                log.warning("Capture du coin de l'écran impossible : %s", e)
            if self.errors == 5:
                self.status.emit(f"⚠ Passer les dialogues : capture impossible ({e}).")
            return
        self.errors = 0
        now = time.time()
        if hud and ok and self.last_score < dialogue.AUTO_THRESHOLD + 0.1 and not self.active:
            ok = False   # mini-map visible et détection pas franche : prudence (icônes du HUD)
        if not ok and 0.38 <= self.last_score < dialogue.THRESHOLD and not hud:
            # cas douteux (jeu devant, pas de mini-map) : on garde l'image pour améliorer la détection
            diag.snapshot("dialogue_douteux", img, min_interval=300)
            if now - self._last_doubt_log > 60:
                self._last_doubt_log = now
                log.info("Détection de dialogue douteuse (score %.2f)", self.last_score)
        if ok:
            self.last_seen = now
            self.hud_since = None
            if not self.active:
                self.active = True
                self.started = now
                self.presses = 0
                self.status.emit("⏩ Dialogue détecté : je passe le texte…")
                log.info("Dialogue détecté (score %.2f)", self.last_score)
                diag.snapshot("dialogue_detecte", img, min_interval=1800)
                self.key_timer.start()
            return
        if not self.active:
            return
        # Plus de boutons de dialogue : est-ce la fin, ou juste un fondu / une cinématique ?
        if hud is None:   # pas de mini-map calibrée : on se contente d'un délai
            if now - self.last_seen > END_AFTER:
                self._finish(x, y)
            return
        if hud:
            self.hud_since = self.hud_since or now
            if now - self.hud_since >= HUD_STABLE:
                self._finish(x, y)
        else:
            self.hud_since = None
            if now - self.last_seen > GIVE_UP:
                log.warning("Dialogue abandonné : ni dialogue ni interface de jeu depuis %.0f s", GIVE_UP)
                self.active = False
                self.key_timer.stop()

    def _hud(self):
        if self.hud_frame is None:
            return None
        try:
            frame = self.hud_frame()
        except Exception:  # noqa: BLE001
            return False
        return None if frame is None else dialogue.hud_visible(frame)

    def _finish(self, x, y):
        self.active = False
        self.key_timer.stop()
        self.hud_since = None
        log.info("Dialogue terminé : %d appuis sur F en %.0f s", self.presses, time.time() - self.started)
        if self.presses >= 2 or self.last_seen - self.started > 1.0:
            self.finished.emit(self.presses)
            self.toast.show_message("✓  Dialogue terminé", near=_qpoint(x, y))

    def _press(self):
        if not self.active or self.hud_since is not None:
            return   # l'interface normale revient : on n'appuie plus
        if time.time() - self.last_seen > KEEP_PRESSING:
            return   # plus de dialogue depuis longtemps (menu ouvert ?) : on n'appuie plus
        front, _pid = self._game_front()
        if not front:
            return   # le jeu n'est pas au premier plan : surtout ne rien taper ailleurs
        try:
            dialogue.press_f()
            self.presses += 1
        except Exception as e:  # noqa: BLE001
            log.error("Impossible d'appuyer sur F : %s", e)
            self.status.emit(f"⚠ Impossible d'appuyer sur F : {e}")
            self.key_timer.stop()


def _qpoint(x, y):
    from PyQt6.QtCore import QPoint
    return QPoint(int(x), int(y))
