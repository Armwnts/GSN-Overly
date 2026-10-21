"""Orientation du joueur, lue sur la mini-map (toujours orientée nord en haut) :
- flèche cyan au centre : direction dans laquelle le personnage regarde ;
- cône blanc translucide : direction de la caméra.
Angles en degrés, dans le sens horaire depuis le nord (0 = haut, 90 = droite)."""
from __future__ import annotations

import math

import cv2
import numpy as np


_ARROW = None      # modèles de la flèche tournés tous les 3°
_S = 41


def _arrow_models():
    global _ARROW
    if _ARROW is None:
        from pathlib import Path
        up = cv2.imread(str(Path(__file__).with_name("assets") / "arrow_up.png"), cv2.IMREAD_GRAYSCALE)
        _ARROW = []
        if up is not None:
            up = (up > 127).astype(np.float32)
            area = float(up.sum())
            for a in range(0, 360, 3):
                M = cv2.getRotationMatrix2D((_S // 2, _S // 2), -a, 1.0)
                _ARROW.append((a, cv2.warpAffine(up, M, (_S, _S), flags=cv2.INTER_LINEAR)))
            _ARROW = (area, _ARROW)
    return _ARROW


def arrow_candidates(bgr: np.ndarray) -> list[tuple[float, float]]:
    """Orientations possibles de la flèche (angle, score). La flèche est presque un triangle
    équilatéral : à cette taille, 3 orientations à 120° se ressemblent ; on choisit ensuite
    grâce au cône de vision et à l'image précédente."""
    n = min(bgr.shape[:2])
    c = n / 2
    r = max(5, int(n * 0.13))
    y0, x0 = int(c - r), int(c - r)
    patch = bgr[y0:y0 + 2 * r, x0:x0 + 2 * r]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (80, 70, 150), (105, 255, 255))
    circ = np.zeros_like(m)
    cv2.circle(circ, (r, r), r, 255, -1)
    m = cv2.bitwise_and(m, circ)
    k, lab, st, _cen = cv2.connectedComponentsWithStats(m)
    if k <= 1:
        return []
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    area = int(st[i, cv2.CC_STAT_AREA])
    models = _arrow_models()
    if area < 12 or not models:
        return []
    ref_area, rot = models
    ys, xs = np.nonzero(lab == i)
    cx, cy = xs.mean(), ys.mean()
    sc = math.sqrt(ref_area / area)
    blob = np.zeros((_S, _S), np.float32)
    X = np.round((xs - cx) * sc + _S // 2).astype(int)
    Y = np.round((ys - cy) * sc + _S // 2).astype(int)
    ok = (X >= 0) & (X < _S) & (Y >= 0) & (Y < _S)
    blob[Y[ok], X[ok]] = 1.0
    if sc > 1.2:
        blob = cv2.morphologyEx(blob, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    scores = np.array([(blob * t).sum() / (blob.sum() + t.sum() - (blob * t).sum() + 1e-6) for _a, t in rot])
    best = scores.max()
    if best < 0.45:
        return []
    out = []
    L = len(scores)
    for j in range(L):
        a0, a1, a2 = scores[j - 1], scores[j], scores[(j + 1) % L]
        if a1 >= a0 and a1 >= a2 and a1 >= best * 0.85:
            den = a0 - 2 * a1 + a2
            off = 0.5 * (a0 - a2) / den if abs(den) > 1e-9 else 0.0
            out.append(((rot[j][0] + 3 * max(-1.0, min(1.0, off))) % 360, float(a1)))
    return sorted(out, key=lambda t: -t[1])


def _diff(a, b):
    return abs((a - b + 540) % 360 - 180)


def arrow_angle(bgr: np.ndarray, hint: float | None = None) -> float | None:
    cands = arrow_candidates(bgr)
    if not cands:
        return None
    if hint is None:
        return cands[0][0]
    # parmi les orientations plausibles, la plus proche de l'indice (cône ou image précédente)
    return min(cands, key=lambda t: _diff(t[0], hint) - 30 * t[1])[0]


def cone_angle(bgr: np.ndarray) -> float | None:
    """Le cône de vision éclaircit un secteur d'environ 90° autour du centre."""
    n = min(bgr.shape[:2])
    c = n / 2
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1].astype(np.float32)
    yy, xx = np.mgrid[0:gray.shape[0], 0:gray.shape[1]]
    dx, dy = xx - c, yy - c
    rr = np.hypot(dx, dy)
    ring = (rr > n * 0.13) & (rr < n * 0.30)
    if ring.sum() < 50:
        return None
    ang = (np.degrees(np.arctan2(dx, -dy)) + 360) % 360
    bins = (ang[ring] // 10).astype(int) % 36
    # le cône est plus clair et moins saturé (voile blanc)
    val = gray[ring] - 0.5 * sat[ring]
    sums = np.bincount(bins, weights=val, minlength=36)
    cnt = np.bincount(bins, minlength=36).astype(np.float32)
    prof = sums / np.maximum(cnt, 1)
    sm = np.array([prof[[(i + k) % 36 for k in range(-4, 5)]].mean() for i in range(36)])
    i = int(np.argmax(sm))
    contrast = sm[i] - np.median(sm)
    if contrast < 8:
        return None
    # affinage : moyenne pondérée autour du maximum
    ks = np.arange(-4, 5)
    w = np.clip(prof[(i + ks) % 36] - np.median(prof), 0, None)
    if w.sum() <= 0:
        return (i * 10 + 5) % 360
    return ((i + (w * ks).sum() / w.sum()) * 10 + 5) % 360


class HeadingFilter:
    """Lisse les angles (la mesure varie un peu d'une image à l'autre)."""

    def __init__(self, k: float = 0.45):
        self.k = k
        self.arrow = None
        self.cone = None

    @staticmethod
    def _mix(old, new, k):
        if new is None:
            return old
        if old is None:
            return new
        d = (new - old + 540) % 360 - 180
        return (old + k * d) % 360

    def update(self, bgr):
        self.cone = self._mix(self.cone, cone_angle(bgr), self.k * 0.8)
        hint = self.arrow if self.arrow is not None else self.cone
        self.arrow = self._mix(self.arrow, arrow_angle(bgr, hint), self.k)
        return self.arrow, self.cone
