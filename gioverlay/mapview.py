"""Carte plein écran du jeu : on retrouve quelle partie de Teyvat est affichée
(position et zoom) pour y dessiner les coffres restants.

Même principe que pour la mini-map : points SIFT appariés avec ceux de la
carte HoYoLAB, puis validation par corrélation d'images pour éviter les erreurs.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .locator import FeatureIndex, preprocess

WORK_W = 1000  # largeur de travail (px) : l'écran est réduit pour la vitesse


@dataclass
class MapView:
    s: float          # px carte par px de l'image de travail
    tx: float         # px carte = s * x + tx
    ty: float
    k: float          # px image de travail par px capturé
    score: float
    inliers: int

    def map_to_capture(self, xy: np.ndarray) -> np.ndarray:
        """Coordonnées carte -> pixels de l'image capturée."""
        return ((xy - np.float32([self.tx, self.ty])) / self.s) / self.k

    def capture_rect_in_map(self, w_cap: int, h_cap: int):
        x0, y0 = self.tx, self.ty
        return x0, y0, x0 + self.s * w_cap * self.k, y0 + self.s * h_cap * self.k


class MapViewLocator:
    MIN_INLIERS = 25
    ACCEPT = 0.35

    def __init__(self, index: FeatureIndex, map_clahe: np.ndarray):
        self.index = index
        self.map = map_clahe
        self.sift = cv2.SIFT_create(nfeatures=2000, contrastThreshold=0.03)
        self.bf = cv2.BFMatcher(cv2.NORM_L2)
        self.last: MapView | None = None
        self._mask_cache = {}

    def reset(self):
        self.last = None

    def ui_mask(self, h, w) -> np.ndarray:
        """Zone utile de la carte : on ignore les bords où se trouve l'interface du jeu."""
        key = (h, w)
        if key not in self._mask_cache:
            m = np.full((h, w), 255, np.uint8)
            m[:int(h * 0.07), :] = 0                          # barre du haut
            m[:int(h * 0.30), :int(w * 0.20)] = 0             # quêtes en haut à gauche
            m[:, :int(w * 0.035)] = 0                         # curseur de zoom à gauche
            m[int(h * 0.86):, int(w * 0.78):] = 0             # nom de la région en bas à droite
            m[int(h * 0.92):, :int(w * 0.06)] = 0             # bouton en bas à gauche
            self._mask_cache[key] = m
        return self._mask_cache[key]

    def locate(self, screen_bgr: np.ndarray) -> MapView | None:
        H0, W0 = screen_bgr.shape[:2]
        k = min(1.0, WORK_W / W0)
        img = cv2.resize(screen_bgr, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else screen_bgr
        g = preprocess(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        h, w = g.shape
        mask = self.ui_mask(h, w)
        kp, des = self.sift.detectAndCompute(g, mask)
        if des is None or len(kp) < self.MIN_INLIERS:
            self.last = None
            return None

        # FLANN sur toute la carte : plus rapide qu'une recherche locale exhaustive,
        # car la carte plein écran couvre souvent une très grande zone.
        view = self._solve(kp, self.index.flann.knnMatch(des, k=2), None, k, g, mask)
        self.last = view
        return view

    def _solve(self, kp, pairs, sub, k, g, mask) -> MapView | None:
        best: dict[int, cv2.DMatch] = {}
        for p in pairs:
            if len(p) < 2:
                continue
            a, b = p
            if a.distance < 0.8 * b.distance:
                j = int(a.trainIdx if sub is None else sub[a.trainIdx])
                if j not in best or a.distance < best[j].distance:
                    best[j] = a
        if len(best) < self.MIN_INLIERS:
            return None
        src = np.float32([kp[m.queryIdx].pt for m in best.values()])
        dst = self.index.xy[np.fromiter(best.keys(), np.int64)]
        M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=8.0,
                                             maxIters=5000, confidence=0.999)
        if M is None:
            return None
        inl = inl.ravel() == 1
        n = int(inl.sum())
        angle = abs(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
        if n < self.MIN_INLIERS or angle > 3:
            return None
        # Ajustement final sans rotation (la carte du jeu est toujours orientée au nord) :
        # moindres carrés sur les inliers pour x_carte = s * x + tx (idem pour y).
        S, D = src[inl].astype(np.float64), dst[inl].astype(np.float64)
        Sm, Dm = S.mean(0), D.mean(0)
        s = float(((S - Sm) * (D - Dm)).sum() / ((S - Sm) ** 2).sum())
        if not (0.05 < s < 50):
            return None
        tx, ty = Dm - s * Sm
        score = self._verify(g, mask, s, tx, ty)
        if score < self.ACCEPT:
            return None
        return MapView(s, float(tx), float(ty), k, score, n)

    def _verify(self, g, mask, s, tx, ty) -> float:
        """Corrélation entre l'écran et la carte projetée au même endroit."""
        h, w = g.shape
        A = np.float32([[s, 0, tx], [0, s, ty]])  # écran -> carte
        warped = cv2.warpAffine(self.map, A, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        valid = (mask > 0) & (warped > 0)
        if valid.sum() < 0.2 * (mask > 0).sum():
            return 0.0
        a = g[valid].astype(np.float32)
        b = warped[valid].astype(np.float32)
        a -= a.mean()
        b -= b.mean()
        den = float(np.sqrt((a * a).sum() * (b * b).sum()))
        return float((a * b).sum() / den) if den > 0 else 0.0
