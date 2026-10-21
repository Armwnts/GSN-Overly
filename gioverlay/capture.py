"""Capture de la zone de la mini-map (ou de tout l'écran) à l'écran."""
from __future__ import annotations

import sys

import numpy as np

IS_WIN = sys.platform == "win32"


class CaptureError(RuntimeError):
    pass


def _qt_screen_at(x: float, y: float):
    """Écran Qt contenant le point (coordonnées Qt), ou None hors application Qt."""
    try:
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            return None
        return QGuiApplication.screenAt(QPoint(int(x), int(y))) or QGuiApplication.primaryScreen()
    except Exception:
        return None


class ScreenCapture:
    """Capture via `mss`. Les zones sont données en coordonnées Qt (points).

    * macOS : mss travaille déjà en points ; sur un écran Retina l'image est plus
      grande que la zone, `scale` (px par point) sert à recaler l'overlay.
    * Windows : mss travaille en pixels physiques alors que Qt utilise des pixels
      « logiques » quand l'affichage est mis à l'échelle (125 %, 150 %…) : on convertit.
    """

    def __init__(self):
        import mss  # import tardif : erreur claire si absent
        self._sct = mss.mss()
        self.black_frames = 0

    # --- conversion Qt (logique) -> pixels de capture ------------------------------
    def _monitor_for(self, screen):
        """Moniteur mss correspondant à un écran Qt (Windows)."""
        g, dpr = screen.geometry(), screen.devicePixelRatio()
        best, best_err = None, None
        for m in self._sct.monitors[1:]:
            err = abs(m["width"] - g.width() * dpr) + abs(m["height"] - g.height() * dpr)
            # à taille égale, on préfère le moniteur à la même position
            err += 0.01 * (min(abs(m["left"] - g.x()), abs(m["left"] - g.x() * dpr))
                           + min(abs(m["top"] - g.y()), abs(m["top"] - g.y() * dpr)))
            if best_err is None or err < best_err:
                best, best_err = m, err
        return best or self._sct.monitors[1]

    def _to_box(self, x, y, w, h) -> dict:
        if IS_WIN:
            scr = _qt_screen_at(x + w / 2, y + h / 2)
            if scr is not None:
                g, dpr = scr.geometry(), scr.devicePixelRatio()
                mon = self._monitor_for(scr)
                return {"left": int(round(mon["left"] + (x - g.x()) * dpr)),
                        "top": int(round(mon["top"] + (y - g.y()) * dpr)),
                        "width": max(1, int(round(w * dpr))), "height": max(1, int(round(h * dpr)))}
        return {"left": int(round(x)), "top": int(round(y)),
                "width": max(1, int(round(w))), "height": max(1, int(round(h)))}

    def _grab(self, box) -> np.ndarray:
        try:
            shot = self._sct.grab(box)
        except Exception as e:  # mss lève des exceptions spécifiques à chaque OS
            raise CaptureError(str(e)) from e
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])  # BGRA -> BGR

    # --- API ------------------------------------------------------------------------
    def grab(self, x: float, y: float, size: float) -> tuple[np.ndarray, float]:
        img = self._grab(self._to_box(x, y, size, size))
        # Image entièrement noire : autorisation « Enregistrement de l'écran » manquante
        # (macOS) ou jeu en plein écran exclusif (Windows).
        self.black_frames = self.black_frames + 1 if img.max() < 8 else 0
        return img, img.shape[1] / size

    @property
    def probably_no_permission(self) -> bool:
        return self.black_frames >= 10

    def grab_game_corner(self, rect, zone) -> tuple[np.ndarray, float]:
        """Comme grab_corner_at, mais relatif à l'image du jeu `rect` = (x, y, largeur, hauteur)
        en points (coordonnées globales). Ne capture que l'intérieur de la fenêtre du jeu."""
        rx, ry, rw, rh = rect
        zx, zy, zw, zh = zone(rw, rh)
        img = self._grab(self._to_box(rx + zx, ry + zy, zw, zh))
        return img, rh * img.shape[0] / max(1.0, zh)

    def grab_corner_at(self, x: float, y: float, zone) -> tuple[np.ndarray, float]:
        """Capture une zone de l'écran qui contient (x, y). `zone(w, h)` donne (x, y, largeur, hauteur)
        relatifs à cet écran (en points). Renvoie (image BGR, hauteur de l'écran en pixels de l'image)."""
        scr = _qt_screen_at(x, y)
        if scr is not None:
            g = scr.geometry()
            gx, gy, gw, gh = g.x(), g.y(), g.width(), g.height()
        else:
            mon = self._sct.monitors[1]
            gx, gy, gw, gh = mon["left"], mon["top"], mon["width"], mon["height"]
        zx, zy, zw, zh = zone(gw, gh)
        img = self._grab(self._to_box(gx + zx, gy + zy, zw, zh))
        return img, gh * img.shape[0] / max(1.0, zh)

    def grab_screen_at(self, x: float, y: float):
        """Capture tout l'écran qui contient le point (x, y).
        Renvoie (image BGR, px par point, rectangle de l'écran en coordonnées Qt)."""
        scr = _qt_screen_at(x, y)
        if scr is not None:
            g = scr.geometry()
            rect = (g.x(), g.y(), g.width(), g.height())
            if IS_WIN:
                box = self._to_box(*rect)
            else:
                box = {"left": g.x(), "top": g.y(), "width": g.width(), "height": g.height()}
        else:
            mons = self._sct.monitors[1:]
            mon = next((m for m in mons if m["left"] <= x < m["left"] + m["width"]
                        and m["top"] <= y < m["top"] + m["height"]), mons[0])
            box = {k: mon[k] for k in ("left", "top", "width", "height")}
            rect = (mon["left"], mon["top"], mon["width"], mon["height"])
        img = self._grab(box)
        return img, img.shape[1] / rect[2], rect
