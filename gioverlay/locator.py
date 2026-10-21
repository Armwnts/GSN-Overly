"""Localisation du joueur : on retrouve la mini-map dans la grande carte.

1. Une fois pour toutes, on calcule des points caractéristiques SIFT sur toute
   la carte (mis en cache dans features_*.npz).
2. À chaque image, on calcule les points SIFT de la mini-map et on les apparie
   avec ceux de la carte :
     - recherche globale (FLANN sur toute la carte) quand on est perdu,
     - recherche locale autour de la dernière position quand on suit le joueur.
3. RANSAC estime l'échelle et la position ; une corrélation d'images (NCC)
   entre la mini-map et la carte valide et affine le résultat.
4. Quand les points SIFT manquent (désert, plaine), la corrélation seule suit
   le joueur, et peut le retrouver sur toute la carte.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

_CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def preprocess(gray: np.ndarray) -> np.ndarray:
    return _CLAHE.apply(gray)


class FeatureIndex:
    """Points SIFT de la carte + grille spatiale pour les recherches locales."""

    CELL = 256

    def __init__(self, xy: np.ndarray, desc: np.ndarray):
        self.xy = np.ascontiguousarray(xy, np.float32)
        self.desc = np.ascontiguousarray(desc, np.float32)
        cells = np.floor(self.xy / self.CELL).astype(np.int64)
        keys = cells[:, 0] * 1_000_000 + cells[:, 1]
        order = np.argsort(keys, kind="stable")
        uniq, starts = np.unique(keys[order], return_index=True)
        ends = np.r_[starts[1:], len(order)]
        self._grid = {int(k): order[s:e] for k, s, e in zip(uniq, starts, ends)}
        self._flann = None

    def __len__(self):
        return len(self.xy)

    # --- construction / cache -------------------------------------------------
    @classmethod
    def build(cls, map_gray: np.ndarray, tile=1024, per_tile=3000, progress=None) -> "FeatureIndex":
        sift = cv2.SIFT_create(nfeatures=per_tile)
        g = preprocess(map_gray)
        H, W = g.shape
        ov = 48
        tiles = [(x, y) for y in range(0, H, tile) for x in range(0, W, tile)]
        xs, ds = [], []
        for i, (x, y) in enumerate(tiles):
            x0, y0 = max(0, x - ov), max(0, y - ov)
            x1, y1 = min(W, x + tile + ov), min(H, y + tile + ov)
            sub = g[y0:y1, x0:x1]
            if progress:
                progress(i + 1, len(tiles))
            if sub.std() < 3:  # océan vide / zone transparente
                continue
            kp, d = sift.detectAndCompute(sub, None)
            if d is None or not kp:
                continue
            pts = np.array([k.pt for k in kp], np.float32) + np.float32([x0, y0])
            keep = (pts[:, 0] >= x) & (pts[:, 0] < x + tile) & (pts[:, 1] >= y) & (pts[:, 1] < y + tile)
            xs.append(pts[keep])
            ds.append(d[keep])
        if not xs:
            raise RuntimeError("Aucun point caractéristique trouvé sur la carte.")
        return cls(np.concatenate(xs), np.concatenate(ds))

    @classmethod
    def load_or_build(cls, map_path: Path, progress=None) -> "FeatureIndex":
        st = os.stat(map_path)
        # Clé du cache : la taille du fichier (la date change quand on copie le dossier).
        cache = map_path.parent / f"features_{st.st_size}.npz"
        for f in [cache, *map_path.parent.glob(f"features_*_{st.st_size}.npz")]:
            if f.exists():
                z = np.load(f)
                return cls(z["xy"], z["desc"])
        gray = cv2.imread(str(map_path), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise RuntimeError(f"Impossible de lire {map_path}")
        idx = cls.build(gray, progress=progress)
        del gray
        for old in map_path.parent.glob("features_*.npz"):
            old.unlink()
        # Les descripteurs SIFT tiennent sur des octets : 4x moins de disque.
        np.savez(cache, xy=idx.xy, desc=np.clip(idx.desc, 0, 255).astype(np.uint8))
        return idx

    # --- requêtes ---------------------------------------------------------------
    def subset(self, cx: float, cy: float, r: float) -> np.ndarray:
        c = self.CELL
        x0, x1 = int(np.floor((cx - r) / c)), int(np.floor((cx + r) / c))
        y0, y1 = int(np.floor((cy - r) / c)), int(np.floor((cy + r) / c))
        parts = [self._grid[gx * 1_000_000 + gy]
                 for gx in range(x0, x1 + 1) for gy in range(y0, y1 + 1)
                 if gx * 1_000_000 + gy in self._grid]
        return np.concatenate(parts) if parts else np.empty(0, np.int64)

    @property
    def flann(self):
        if self._flann is None:
            m = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=64))
            m.add([self.desc])
            m.train()
            self._flann = m
        return self._flann


@dataclass
class LocateResult:
    M: np.ndarray          # 2x3 : pixel mini-map -> pixel carte
    Minv: np.ndarray       # 2x3 : pixel carte -> pixel mini-map
    pos: tuple[float, float]  # position du joueur (px carte)
    scale: float           # px carte par px mini-map
    angle: float           # degrés (la mini-map de Genshin est toujours orientée au nord)
    inliers: int           # repères SIFT (0 si trouvé par corrélation seule)
    mode: str              # "suivi", "corrélation", "global", "global-corr"
    score: float = 0.0     # score de corrélation (0..1) qui a validé la position


def _similarity(pos, s, size) -> np.ndarray:
    """Transformation mini-map -> carte, sans rotation (mini-map orientée au nord)."""
    c = size / 2.0
    return np.array([[s, 0.0, pos[0] - s * c], [0.0, s, pos[1] - s * c]], np.float64)


def _subpixel(res, loc):
    """Affinage sous-pixel du maximum par une parabole sur chaque axe."""
    x, y = loc
    dx = dy = 0.0
    if 0 < x < res.shape[1] - 1:
        l, c, r = res[y, x - 1], res[y, x], res[y, x + 1]
        d = l - 2 * c + r
        if d < 0:
            dx = float(np.clip(0.5 * (l - r) / d, -0.5, 0.5))
    if 0 < y < res.shape[0] - 1:
        u, c, b = res[y - 1, x], res[y, x], res[y + 1, x]
        d = u - 2 * c + b
        if d < 0:
            dy = float(np.clip(0.5 * (u - b) / d, -0.5, 0.5))
    return x + dx, y + dy


def looks_like_minimap(bgr: np.ndarray) -> bool:
    """La flèche cyan du joueur est toujours au centre de la mini-map. Sans elle,
    la mini-map n'est pas affichée (menu, carte ouverte, autre fenêtre…)."""
    if bgr.ndim != 3:
        return True
    n = min(bgr.shape[:2])
    c = n // 2
    r = max(4, int(n * 0.12))
    hsv = cv2.cvtColor(bgr[c - r:c + r, c - r:c + r], cv2.COLOR_BGR2HSV)
    m = np.zeros(hsv.shape[:2], np.uint8)
    cv2.circle(m, (r, r), r, 255, -1)
    cyan = cv2.inRange(hsv, (80, 90, 140), (105, 255, 255))
    return float((cyan[m > 0] > 0).mean()) > 0.06


class Locator:
    """Combine deux méthodes complémentaires :

    * SIFT (points caractéristiques) : marche quelle que soit l'échelle — utile
      car la mini-map zoome dans les villes — mais échoue sur les zones pauvres
      en détails (désert, plaines).
    * Corrélation d'images (NCC) : compare toute la mini-map à la carte. Très
      fiable même dans le désert, mais demande de connaître l'échelle.

    Chaque position est validée par la corrélation, ce qui élimine les faux positifs.
    """

    MASK_R = 0.42      # le bord de la mini-map est translucide (on y voit le jeu)
    CENTER_R = 0.12    # flèche du joueur (et point de téléportation sous ses pieds)
    ACCEPT = 0.50      # score de corrélation minimal pour accepter une position
    ACCEPT_WIDE = 0.60 # recherche large (après une perte) : plus exigeant
    MARGIN = 0.12      # écart minimal avec le 2e meilleur pic (recherches larges)
    COARSE = 4         # facteur de réduction pour la recherche globale par corrélation
    SWEEP = [0.3 * 1.22 ** i for i in range(11)]  # 0,3 → 2,2 : échelles testées sans référence

    def __init__(self, index: FeatureIndex, map_gray: np.ndarray | None = None, min_inliers=10,
                 lost_after=15, track_radius_factor=4.0, known_scales=None):
        self.index = index
        self.min_inliers = min_inliers
        self.lost_after = lost_after
        self.track_radius_factor = track_radius_factor
        self.sift = cv2.SIFT_create(nfeatures=800, contrastThreshold=0.03)
        self.bf = cv2.BFMatcher(cv2.NORM_L2)
        self.map = None
        self.coarse = None
        if map_gray is not None:
            self.map = preprocess(map_gray)
            self.coarse = cv2.resize(self.map, None, fx=1 / self.COARSE, fy=1 / self.COARSE,
                                     interpolation=cv2.INTER_AREA).astype(np.float32)
        # Échelles déjà rencontrées (extérieur, villes…), utilisées par la recherche globale.
        self.known_scales: list[float] = list(known_scales or [])
        self.last: LocateResult | None = None
        self.last_known: LocateResult | None = None
        self.since_known = 0
        self.failures = 0
        self._next_global = 0.0
        self._next_sweep = 0.0
        self._global_fails = 0
        self._M_smooth = None
        self.minimap_visible = True
        self.hidden_frames = 0

    # ------------------------------------------------------------------ outils
    def reset(self) -> None:
        self.last = None
        self.last_known = None
        self.failures = 0
        self._M_smooth = None
        self._next_global = 0.0

    @classmethod
    def circle_mask(cls, size: int, extra_mask_points=(), extra_r=0) -> np.ndarray:
        m = np.zeros((size, size), np.uint8)
        c = size // 2
        cv2.circle(m, (c, c), int(size * cls.MASK_R), 255, -1)
        cv2.circle(m, (c, c), int(size * cls.CENTER_R), 0, -1)
        for (x, y) in extra_mask_points:
            cv2.circle(m, (int(x), int(y)), int(extra_r), 0, -1)
        return m

    def _remember_scale(self, s: float) -> None:
        for i, k in enumerate(self.known_scales):
            if abs(s / k - 1) < 0.12:
                self.known_scales[i] = 0.9 * k + 0.1 * s
                return
        self.known_scales.append(s)
        self.known_scales = self.known_scales[-4:]

    def _ncc(self, g, mask, cx, cy, s, r, src=None, step=1):
        """Cherche la mini-map (échelle s) autour de (cx, cy) dans un rayon r.
        Renvoie (score, 2e pic, (x, y)) ou None."""
        src = self.map if src is None else src
        n = g.shape[0]
        ns = int(round(n * s / step))
        if ns < 12:
            return None
        interp = cv2.INTER_AREA if ns < n else cv2.INTER_LINEAR
        t = cv2.resize(g, (ns, ns), interpolation=interp)
        tm = cv2.resize(mask, (ns, ns), interpolation=cv2.INTER_NEAREST)
        cx, cy, r = cx / step, cy / step, r / step
        H, W = src.shape
        x0, y0 = int(np.floor(cx - ns / 2 - r)), int(np.floor(cy - ns / 2 - r))
        x1, y1 = int(np.ceil(cx + ns / 2 + r)) + 1, int(np.ceil(cy + ns / 2 + r)) + 1
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        crop = src[y0:y1, x0:x1]
        if crop.shape[0] < ns + 2 or crop.shape[1] < ns + 2 or crop.std() < 2:
            return None
        res = cv2.matchTemplate(crop, t, cv2.TM_CCOEFF_NORMED, mask=tm)
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, best, _, loc = cv2.minMaxLoc(res)
        second = -1.0
        if res.size > 400:
            r2 = res.copy()
            cv2.circle(r2, loc, max(3, int(12 / step)), -1.0, -1)
            second = float(r2.max())
        fx, fy = _subpixel(res, loc)
        eff = ns / n  # échelle réellement utilisée après arrondi
        px = (x0 + fx + n * eff / 2) * step
        py = (y0 + fy + n * eff / 2) * step
        return float(best), second, (px, py)

    # ------------------------------------------------------------- principal
    def locate(self, minimap_bgr: np.ndarray, extra_mask_points=(), extra_r=0) -> LocateResult | None:
        h, w = minimap_bgr.shape[:2]
        size = min(h, w)
        img = minimap_bgr[:size, :size]
        self.minimap_visible = looks_like_minimap(img)
        if not self.minimap_visible:
            # Menu, chargement, autre fenêtre… On ne compte pas d'échec, mais après
            # une longue absence (téléportation possible) on ne suit plus « à l'aveugle ».
            self.hidden_frames += 1
            if self.hidden_frames >= 8 and self.last is not None:
                self.last = None
                self._M_smooth = None
            return None
        self.hidden_frames = 0
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        g = preprocess(gray)
        mask = self.circle_mask(size, extra_mask_points, extra_r)
        if cv2.meanStdDev(g, mask=mask)[1][0][0] < 6:   # écran uni (chargement, menu…)
            return self._fail()

        res = None
        if self.last is not None:
            res = self._sift_track(g, mask, size)
            if res is None:
                r = 40 + 20 * self.failures
                res = self._ncc_track(g, mask, size, self.last, r=r, wide=r > 60)
        elif self.last_known is not None and self.since_known < 100:
            # perdu depuis peu : le joueur est sûrement encore dans le coin
            res = self._ncc_track(g, mask, size, self.last_known, r=350, wide=True)
        if res is None:
            res = self._sift_global(g, mask, size)
        if res is None and self.coarse is not None:
            res = self._global_corr(g, mask, size)
        if res is None:
            return self._fail()
        return self._accept(res, size)

    def probe(self, minimap_bgr: np.ndarray, extra_mask_points=(), extra_r=0) -> LocateResult | None:
        """Recherche globale seule, sans rien mémoriser : sert à savoir si la mini-map montre
        cette carte-ci (autre dimension)."""
        h, w = minimap_bgr.shape[:2]
        size = min(h, w)
        img = minimap_bgr[:size, :size]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        g = preprocess(gray)
        mask = self.circle_mask(size, extra_mask_points, extra_r)
        if cv2.meanStdDev(g, mask=mask)[1][0][0] < 6:
            return None
        res = self._sift_global(g, mask, size)
        if res is None:
            return None
        if self.map is None and res.inliers < 20:   # sans vérification par l'image : plus exigeant
            return None
        return res

    def _accept(self, res: LocateResult, size: int) -> LocateResult:
        c = size / 2.0
        if self._M_smooth is not None and res.mode in ("suivi", "corrélation"):
            d = np.hypot(*((res.M - self._M_smooth) @ np.array([c, c, 1.0])))
            alpha = 0.5 if d < 2.5 else 1.0
            self._M_smooth = (1 - alpha) * self._M_smooth + alpha * res.M
        else:
            self._M_smooth = res.M.copy()
        M = self._M_smooth
        pos = M @ np.array([c, c, 1.0])
        out = LocateResult(M, cv2.invertAffineTransform(M), (float(pos[0]), float(pos[1])), res.scale,
                           0.0, res.inliers, res.mode, res.score)
        self.last = self.last_known = out
        self.since_known = 0
        self.failures = 0
        self._remember_scale(res.scale)
        return out

    def _fail(self):
        self.failures += 1
        self.since_known += 1
        if self.failures >= self.lost_after:
            self.last = None
            self._M_smooth = None
        return None

    def _verify(self, g, mask, size, pos, s, mode, inliers=0, r=8.0):
        """Valide (et affine) une position par corrélation."""
        if self.map is None:
            return LocateResult(_similarity(pos, s, size), None, pos, s, 0.0, inliers, mode, 1.0)
        out = self._ncc(g, mask, pos[0], pos[1], s, r)
        if out is None or out[0] < self.ACCEPT:
            return None
        score, _, p = out
        return LocateResult(_similarity(p, s, size), None, p, s, 0.0, inliers, mode, score)

    # --- suivi -----------------------------------------------------------------------
    def _ncc_track(self, g, mask, size, ref: LocateResult, r, wide=False):
        if self.map is None:
            return None
        best = None
        for f in (1.0, 0.94, 1.06):
            out = self._ncc(g, mask, ref.pos[0], ref.pos[1], ref.scale * f, r)
            if out and (best is None or out[0] > best[0][0]):
                best = (out, ref.scale * f)
        if best is None:
            return None
        (score, second, p), s = best
        need = self.ACCEPT_WIDE if wide else self.ACCEPT
        if size * s < 60:  # mini-map très zoomée (ville) : petit motif, plus d'ambiguïté
            need += 0.05
        if score < need or (wide and score - second < self.MARGIN):
            return None
        return LocateResult(_similarity(p, s, size), None, p, s, 0.0, 0, "corrélation", score)

    def _sift_features(self, g, mask, size):
        k = 1.0
        gg, mm = g, mask
        if size > 360:
            k = 360 / size
            gg = cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
            mm = cv2.resize(mask, (gg.shape[1], gg.shape[0]), interpolation=cv2.INTER_NEAREST)
        kp, des = self.sift.detectAndCompute(gg, mm)
        return kp, des, k

    def _sift_track(self, g, mask, size):
        kp, des, k = self._sift_features(g, mask, size)
        if des is None or len(kp) < 6:
            return None
        r = (size / 2.0) * self.last.scale * self.track_radius_factor
        sub = self.index.subset(self.last.pos[0], self.last.pos[1], r)
        if len(sub) < 10:
            return None
        pairs = self.bf.knnMatch(des, self.index.desc[sub], k=2)
        est = self._estimate(kp, pairs, k, size, sub, ratio=0.8, need=max(6, self.min_inliers - 2))
        if est is None:
            return None
        s, pos, n = est
        if not (0.75 < s / self.last.scale < 1.33):
            return None
        return self._verify(g, mask, size, pos, s, "suivi", n)

    # --- recherche globale ---------------------------------------------------------
    def _sift_global(self, g, mask, size):
        kp, des, k = self._sift_features(g, mask, size)
        if des is None or len(kp) < 8:
            return None
        pairs = self.index.flann.knnMatch(des, k=2)
        est = self._estimate(kp, pairs, k, size, None, ratio=0.8, need=6)
        if est is None:
            return None
        s, pos, n = est
        return self._verify(g, mask, size, pos, s, "global", n, r=10.0)

    def _global_corr(self, g, mask, size):
        """Recherche par corrélation sur toute la carte (~2 s). Aux échelles déjà
        connues d'abord ; on rebalaye toutes les échelles au premier lancement
        ou après plusieurs échecs (ex. : première visite d'une ville zoomée)."""
        now = time.monotonic()
        if now < self._next_global:
            return None
        sweep = (not self.known_scales or self._global_fails >= 4) and now >= self._next_sweep
        if not sweep and not self.known_scales:
            return None
        res = self._ncc_global(g, mask, size, self.SWEEP if sweep else self.known_scales)
        if sweep:
            self._next_sweep = time.monotonic() + 8.0
            self._global_fails = 0
        elif res is None:
            self._global_fails += 1
        self._next_global = time.monotonic() + 0.3
        return res

    def _ncc_global(self, g, mask, size, scales):
        """Recherche sur toute la carte réduite, aux échelles déjà rencontrées,
        puis vérification des meilleurs candidats en pleine résolution."""
        cands = []
        src = self.coarse
        for s in scales:
            n = size
            ns = int(round(n * s / self.COARSE))
            if ns < 10:
                continue
            t = cv2.resize(g, (ns, ns), interpolation=cv2.INTER_AREA).astype(np.float32)
            tm = cv2.resize(mask, (ns, ns), interpolation=cv2.INTER_NEAREST)
            res = cv2.matchTemplate(src, t, cv2.TM_CCOEFF_NORMED, mask=tm)
            res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
            for _ in range(3 if scales is self.SWEEP else 4):  # meilleurs pics distincts
                _, v, _, loc = cv2.minMaxLoc(res)
                if v < 0.3:
                    break
                cands.append((v, s, ((loc[0] + ns / 2) * self.COARSE, (loc[1] + ns / 2) * self.COARSE)))
                cv2.circle(res, loc, 10, -1.0, -1)
        best = None
        sweep = scales is self.SWEEP
        # Les scores grossiers ne se comparent pas d'une échelle à l'autre : en
        # balayage, on vérifie tous les candidats ; sinon les 10 meilleurs.
        for _, s, p in (cands if sweep else sorted(cands, reverse=True)[:10]):
            for f in ((0.92, 1.0, 1.08) if sweep else (1.0,)):
                out = self._ncc(g, mask, p[0], p[1], s * f, 3 * self.COARSE)
                need = self.ACCEPT + 0.08 + (0.05 if size * s * f < 60 else 0.0)
                if out and out[0] >= need and (best is None or out[0] > best[0]):
                    best = (out[0], s * f, out[2])
        if best is None:
            return None
        score, s, p = best
        return LocateResult(_similarity(p, s, size), None, p, s, 0.0, 0, "global-corr", score)

    def _estimate(self, kp, pairs, k, size, sub, ratio, need):
        """Appariements -> (échelle, position du centre, nb d'inliers) via RANSAC."""
        idx = self.index
        best: dict[int, cv2.DMatch] = {}
        for p in pairs:
            if len(p) < 2:
                continue
            a, b = p
            if a.distance < ratio * b.distance:
                j = int(a.trainIdx if sub is None else sub[a.trainIdx])
                if j not in best or a.distance < best[j].distance:
                    best[j] = a
        if len(best) < need:
            return None
        src = np.float32([(kp[m.queryIdx].pt[0] / k, kp[m.queryIdx].pt[1] / k) for m in best.values()])
        dst = idx.xy[np.fromiter(best.keys(), np.int64)]
        M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=6.0,
                                             maxIters=5000, confidence=0.999)
        if M is None:
            return None
        n = int(inl.sum())
        if n < need:
            return None
        s = float(np.hypot(M[0, 0], M[1, 0]))
        angle = abs(float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))))
        if not (0.05 < s < 20) or angle > 8:   # la mini-map ne tourne pas
            return None
        spread = np.ptp(src[inl.ravel() == 1], axis=0)
        if spread.min() < size * 0.12:
            return None
        c = size / 2.0
        pos = M @ np.array([c, c, 1.0])
        return s, (float(pos[0]), float(pos[1])), n
