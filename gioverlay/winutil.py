"""Réglages spécifiques à Windows (via ctypes). Sans effet sur les autres systèmes."""
from __future__ import annotations

import sys

IS_WIN = sys.platform == "win32"

WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004 et plus récent


def exclude_from_capture(widget) -> bool:
    """Rend la fenêtre invisible pour les captures d'écran : l'overlay ne se voit
    pas lui-même quand on capture la mini-map ou la carte (il reste visible pour toi)."""
    if not IS_WIN:
        return False
    try:
        import ctypes
        hwnd = int(widget.winId())
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception as e:  # pragma: no cover - dépend de la version de Windows
        print(f"[Windows] exclusion de la capture impossible : {e}")
        return False
