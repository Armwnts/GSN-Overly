"""Passer les dialogues automatiquement.

Pendant un dialogue, Genshin affiche en haut à gauche 4 boutons : « Auto », liste, masquer
et haut-parleur. On les repère par capture d'écran ; tant qu'ils sont visibles, l'app appuie
sur F à la place du joueur. Le dialogue est terminé quand l'interface normale revient.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

ASSETS = Path(__file__).with_name("assets")
REF_H = 1080                       # le modèle a été pris sur un écran de 1080 px de haut
WORK = 0.5                         # on travaille à mi-résolution (plus rapide, assez précis)
# Centres des 3 disques (liste, masquer, haut-parleur) dans le modèle, et leur rayon
_CENTERS = [(22.7, 22.5), (97.2, 22.7), (171.8, 22.4)]
_RADIUS = 20
# Variantes : les 3 disques, ou seulement 2 (certains dialogues n'ont pas tous les boutons)
VARIANTS = [(0, 1, 2), (0, 1), (1, 2)]
SCALES = [0.70, 0.76, 0.82, 0.88, 0.94, 1.0, 1.06, 1.12, 1.19, 1.26, 1.34]
THRESHOLD = 0.55


def _load_templates():
    tpl = cv2.imread(str(ASSETS / "dialogue_icons.png"), cv2.IMREAD_GRAYSCALE)
    if tpl is None:
        return []
    out = []
    for var in VARIANTS:
        x0 = int(_CENTERS[var[0]][0] - _RADIUS - 2)
        x1 = int(_CENTERS[var[-1]][0] + _RADIUS + 3)
        x0, x1 = max(0, x0), min(tpl.shape[1], x1)
        sub = tpl[:, x0:x1]
        centers = [(_CENTERS[i][0] - x0, _CENTERS[i][1]) for i in var]
        mask = np.zeros(sub.shape, np.uint8)
        for cx, cy in centers:
            cv2.circle(mask, (int(round(cx)), int(round(cy))), _RADIUS, 255, -1)
        per_scale = []
        for sc in SCALES:
            t = cv2.resize(sub, None, fx=sc * WORK, fy=sc * WORK, interpolation=cv2.INTER_AREA)
            m = cv2.resize(mask, (t.shape[1], t.shape[0]), interpolation=cv2.INTER_NEAREST)
            per_scale.append((sc, t.astype(np.float32), m))
        out.append((centers, per_scale))
    return out


_TPLS = None
_last_scale = None       # échelle trouvée la dernière fois : essayée en premier


def region_of_screen(w: float, h: float) -> tuple[float, float, float, float]:
    """Zone à capturer (x, y, largeur, hauteur) relative à l'écran du jeu : le coin haut gauche."""
    return 0.0, 0.0, min(w, h * 0.62), h * 0.13


def _match(gray, centers, sc, t, m, min_discs):
    if gray.shape[0] < t.shape[0] or gray.shape[1] < t.shape[1]:
        return 0.0
    r = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED, mask=m)
    r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
    _, mx, _, loc = cv2.minMaxLoc(r)
    if mx < THRESHOLD * 0.8:
        return float(mx)
    return float(mx) if _discs_look_right(gray, loc, centers, sc) >= min_discs else 0.0


# Bouton « ▶ Auto » : toujours présent pendant un dialogue, y compris quand les autres boutons
# sont grisés (choix de réponse, boutons indisponibles). Sa position est fixe : en haut à gauche.
AUTO_THRESHOLD = 0.75
AUTO_SCALES = [0.85, 0.92, 1.0, 1.08, 1.16]
AUTO_MAX_X = 0.13          # le bouton commence à moins de 13 % de la hauteur du jeu du bord gauche
AUTO_MAX_Y = 0.06
_AUTO = None


def _load_auto():
    t = cv2.imread(str(ASSETS / "dialogue_auto.png"), cv2.IMREAD_GRAYSCALE)
    if t is None:
        return []
    return [(sc, cv2.resize(t, None, fx=sc * WORK, fy=sc * WORK, interpolation=cv2.INTER_AREA).astype(np.float32))
            for sc in AUTO_SCALES]


def _auto_score(gray, positional: bool) -> float:
    global _AUTO
    if _AUTO is None:
        _AUTO = _load_auto()
    best = 0.0
    lim_x, lim_y = AUTO_MAX_X * REF_H * WORK, AUTO_MAX_Y * REF_H * WORK
    for sc, t in _AUTO:
        if gray.shape[0] < t.shape[0] or gray.shape[1] < t.shape[1]:
            continue
        r = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
        r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
        if positional:   # seulement là où le bouton peut être
            r = r[:int(lim_y) + 1, :int(lim_x) + 1]
            if r.size == 0:
                continue
        mx = float(r.max())
        if not positional:
            mx -= 0.05   # sans position connue de la fenêtre du jeu : un peu plus exigeant
        best = max(best, mx)
    return best


def detect(region_bgr: np.ndarray, screen_h_px: float, positional: bool = False) -> tuple[bool, float]:
    """`region_bgr` : capture du coin haut gauche ; `screen_h_px` : hauteur de l'écran (ou de la
    fenêtre du jeu) en pixels de cette capture ; `positional` : la capture commence exactement au
    coin de l'image du jeu. Renvoie (dialogue en cours ?, score)."""
    global _TPLS, _last_scale
    if _TPLS is None:
        _TPLS = _load_templates()
    if region_bgr is None or region_bgr.size == 0:
        return False, 0.0
    f = REF_H * WORK / max(1.0, screen_h_px)
    img = cv2.resize(region_bgr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if float(gray.std()) < 1.5:        # image unie (noire) : rien à chercher
        return False, 0.0
    auto = _auto_score(gray, positional)
    if auto >= AUTO_THRESHOLD:
        # rapporté sur l'échelle des disques pour garder des scores comparables dans le journal
        return True, auto
    if not _TPLS:
        return False, auto
    # Ordre d'essai des échelles : d'abord autour de la dernière trouvée (rapide)
    idx = list(range(len(SCALES)))
    if _last_scale is not None:
        idx.sort(key=lambda i: abs(i - _last_scale))
    best = 0.0
    for n, i in enumerate(idx):
        for k, (centers, per_scale) in enumerate(_TPLS):
            sc, t, m = per_scale[i]
            # 3 disques : 2 bien visibles suffisent ; 2 disques : les 2 doivent l'être
            score = _match(gray, centers, sc, t, m, min_discs=2)
            if k > 0:
                score *= 0.97   # variante à 2 disques : un peu moins sûre
            if score > best:
                best = score
            if best >= THRESHOLD + 0.15:
                _last_scale = i
                return True, best
        if _last_scale is not None and n == 2 and best < THRESHOLD * 0.6:
            continue
    if best >= THRESHOLD:
        return True, best
    return False, max(best, auto)


def _discs_look_right(gray, loc, centers, s) -> int:
    """Nombre de disques clairs (beiges) avec un pictogramme sombre au centre."""
    x0, y0 = loc
    good = 0
    for cx, cy in centers:
        x, y = int(x0 + cx * s * WORK), int(y0 + cy * s * WORK)
        r = max(3, int(_RADIUS * s * WORK))
        patch = gray[max(0, y - r):y + r, max(0, x - r):x + r]
        if patch.size == 0:
            continue
        bright = float(np.percentile(patch, 75))
        dark = float(np.percentile(patch, 10))
        if bright >= 150 and bright - dark >= 40:
            good += 1
    return good


def hud_visible(minimap_bgr) -> bool:
    """Interface de jeu normale revenue : mini-map (avec la flèche du perso) et Paimon en haut à gauche."""
    if minimap_bgr is None or minimap_bgr.size == 0:
        return False
    from .locator import looks_like_minimap
    return bool(looks_like_minimap(minimap_bgr))


# ---------------------------------------------------------------------------- touche F
def _mac_keycode_f() -> int:
    try:
        from .macos import keycode_for
        kc = keycode_for("f")
        return 3 if kc is None else kc
    except Exception:
        return 3


def can_send_keys() -> bool:
    if sys.platform != "darwin":
        return True
    try:
        from Quartz import CGPreflightPostEventAccess
        return bool(CGPreflightPostEventAccess())
    except Exception:
        try:
            from ApplicationServices import AXIsProcessTrusted
            return bool(AXIsProcessTrusted())
        except Exception:
            return True


def request_key_permission():
    """Affiche la demande d'autorisation macOS (Accessibilité) pour envoyer des touches."""
    if sys.platform != "darwin":
        return
    try:
        from Quartz import CGRequestPostEventAccess
        CGRequestPostEventAccess()
        return
    except Exception:
        pass
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:
        pass


def press_f():
    if sys.platform == "darwin":
        kc = _mac_keycode_f()
        try:
            from Quartz import CGEventCreateKeyboardEvent, CGEventPost, kCGHIDEventTap
            CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, kc, True))
            time.sleep(0.03)
            CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, kc, False))
        except ImportError:   # PyObjC Quartz absent : appel direct de CoreGraphics
            import ctypes
            cg = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
            cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
            cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
            cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
            cg.CFRelease.argtypes = [ctypes.c_void_p]
            for down in (True, False):
                ev = cg.CGEventCreateKeyboardEvent(None, kc, down)
                cg.CGEventPost(0, ev)
                cg.CFRelease(ev)
                if down:
                    time.sleep(0.03)
    elif sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        def send(flags):
            inp = INPUT(type=1)
            inp.ki = KEYBDINPUT(0, 0x21, 0x0008 | flags, 0, 0)   # scancode de F
            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        send(0)
        time.sleep(0.03)
        send(0x0002)


TITLE_BAR = 28     # barre de titre d'une fenêtre macOS (points)


def game_rect(pid) -> tuple[float, float, float, float] | None:
    """Rectangle (x, y, largeur, hauteur) en points de l'image du jeu dans la fenêtre de
    l'application `pid` (GeForce NOW, Genshin…) : sa plus grande fenêtre visible, sans la barre
    de titre, réduite au format 16:9 centré (bandes noires du streaming). None si inconnu."""
    if pid is None or pid < 0 or sys.platform != "darwin":
        return None
    try:
        from Quartz import (CGWindowListCopyWindowInfo, kCGNullWindowID,
                            kCGWindowListExcludeDesktopElements, kCGWindowListOptionOnScreenOnly)
        wins = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
                                          kCGNullWindowID) or []
        best = None
        for w in wins:
            if int(w.get("kCGWindowOwnerPID", -1)) != pid or int(w.get("kCGWindowLayer", 1)) != 0:
                continue
            b = w.get("kCGWindowBounds") or {}
            x, y, ww, hh = (float(b.get(k, 0)) for k in ("X", "Y", "Width", "Height"))
            if ww * hh > (best[2] * best[3] if best else 0):
                best = (x, y, ww, hh)
        if best is None or best[2] < 200 or best[3] < 150:
            return None
        x, y, w, h = best
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QGuiApplication
        scr = QGuiApplication.screenAt(QPoint(int(x + w / 2), int(y + h / 2)))
        full = scr is not None and abs(scr.geometry().height() - h) < 2 and abs(scr.geometry().width() - w) < 2
        if not full and h > TITLE_BAR * 4:
            y, h = y + TITLE_BAR, h - TITLE_BAR
        if w / h > 16 / 9:                  # bandes noires à gauche et à droite
            nw = h * 16 / 9
            x, w = x + (w - nw) / 2, nw
        elif w / h < 16 / 9:                # bandes noires en haut et en bas
            nh = w * 9 / 16
            y, h = y + (h - nh) / 2, nh
        return x, y, w, h
    except Exception:
        return None


def frontmost_app() -> tuple[int, str]:
    """(pid, nom) de l'application au premier plan."""
    try:
        if sys.platform == "darwin":
            from AppKit import NSWorkspace
            a = NSWorkspace.sharedWorkspace().frontmostApplication()
            return int(a.processIdentifier()), str(a.localizedName() or "")
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return int(pid.value), ""
    except Exception:
        pass
    return -1, ""
