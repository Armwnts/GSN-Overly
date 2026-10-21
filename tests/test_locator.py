"""Test de la localisation sur une carte synthétique (aucune donnée HoYoLAB requise).

    python -m pytest tests/        ou        python tests/test_locator.py
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gioverlay.locator import FeatureIndex, Locator  # noqa: E402


def make_map(w=5000, h=3500, seed=1):
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), (60, 110, 70), np.uint8)
    noise = cv2.GaussianBlur(rng.integers(0, 255, (h // 8, w // 8), np.uint8), (0, 0), 3)
    img = cv2.addWeighted(img, 0.7, cv2.cvtColor(cv2.resize(noise, (w, h)), cv2.COLOR_GRAY2BGR), 0.3, 0)
    for _ in range(900):  # rochers, maisons, lacs…
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        if rng.random() < 0.5:
            cv2.circle(img, (x, y), int(rng.integers(6, 60)), c, -1)
        else:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(8, 80)), y + int(rng.integers(8, 80))), c, -1)
    for _ in range(150):  # chemins
        pts = rng.integers(0, [w, h], (4, 2)).astype(np.int32)
        cv2.polylines(img, [pts], False, (170, 190, 200), int(rng.integers(3, 9)))
    return img


def make_minimap(world, cx, cy, s, angle, size=220):
    """Ce que le jeu afficherait : 1 px mini-map = s px carte, tourné de `angle`."""
    c = size / 2
    # Transformation mini-map -> carte, inversée pour échantillonner la carte.
    th = np.radians(angle)
    A = s * np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    t = np.array([cx, cy]) - A @ np.array([c, c])
    M = cv2.invertAffineTransform(np.hstack([A, t[:, None]]))
    mm = cv2.warpAffine(world, M, (size, size))
    mask = np.zeros((size, size), np.uint8)
    cv2.circle(mask, (int(c), int(c)), int(size * 0.48), 255, -1)
    mm[mask == 0] = (20, 20, 20)
    cv2.fillPoly(mm, [np.int32([[c, c - 12], [c - 8, c + 8], [c + 8, c + 8]])], (255, 220, 60))  # flèche cyan
    noise = np.random.default_rng(0).normal(0, 6, mm.shape)
    return np.clip(mm + noise, 0, 255).astype(np.uint8)


def test_locator():
    world = make_map()
    gray = cv2.cvtColor(world, cv2.COLOR_BGR2GRAY)
    t = time.time()
    idx = FeatureIndex.build(gray)
    print(f"index : {len(idx)} points en {time.time() - t:.1f}s")
    loc = Locator(idx, gray)

    path = [(1200, 900), (1215, 905), (1232, 912), (1250, 925), (3600, 2500), (3610, 2490)]
    errs = []
    for i, (x, y) in enumerate(path):
        mm = make_minimap(world, x, y, s=0.8, angle=0)
        t = time.time()
        r = loc.locate(mm)
        dt = (time.time() - t) * 1000
        assert r is not None or i == 4, f"échec à {x},{y}"
        if r is None:  # téléportation : le suivi échoue d'abord, puis recherche globale
            r = loc.locate(mm)
        assert r is not None, f"échec à {x},{y} après relance"
        e = np.hypot(r.pos[0] - x, r.pos[1] - y)
        errs.append(e)
        print(f"{r.mode:>6}  vrai=({x},{y})  trouvé=({r.pos[0]:.1f},{r.pos[1]:.1f})  "
              f"err={e:.1f}px  échelle={r.scale:.2f}  inliers={r.inliers}  {dt:.0f}ms")
    assert max(errs) < 4.0

    # Autre échelle (la mini-map zoome dans les villes).
    loc.reset()
    mm = make_minimap(world, 2500, 1700, s=0.45, angle=0)
    for _ in range(30):  # le balayage des échelles se déclenche après quelques échecs
        loc._next_global = 0.0  # en vrai, les recherches globales sont espacées dans le temps
        r = loc.locate(mm)
        if r is not None:
            break
    assert r is not None and np.hypot(r.pos[0] - 2500, r.pos[1] - 1700) < 5
    print(f"échelle 0.45 : ok (échelle trouvée {r.scale:.2f})")

    # Écran sans mini-map (menu) : rien n'est renvoyé.
    assert loc.locate(np.full((220, 220, 3), 200, np.uint8)) is None and not loc.minimap_visible

    # Projection d'un point de la carte vers la mini-map (utilisée par l'overlay).
    p = r.Minv @ np.array([2500, 1700, 1.0])  # noqa
    assert np.hypot(p[0] - 110, p[1] - 110) < 4


if __name__ == "__main__":
    test_locator()
    print("OK")
