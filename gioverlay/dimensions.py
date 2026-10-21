"""Dimensions (cartes séparées de Teyvat sur la carte interactive HoYoLAB) : Enkanomiya, le Gouffre…

Chaque dimension a son propre dossier <données>/maps/<id> (carte, points, progression). Teyvat est
livrée avec l'app ; les autres sont téléchargées depuis HoYoLAB la première fois qu'on les choisit
(quelques Mo, moins d'une minute).
"""
from __future__ import annotations

import json
import logging

from PyQt6.QtCore import QThread, pyqtSignal

from .config import map_dir

log = logging.getLogger("gio.dimensions")

TEYVAT = 2
# (id HoYoLAB, nom affiché)
DIMENSIONS = [
    (2, "Teyvat"),
    (7, "Enkanomiya"),
    (9, "Mines souterraines du Gouffre"),
    (34, "Mer d'antan"),
    (36, "Montagne sacrée ancienne"),
    (37, "Temple de l'espace"),
    (40, "Lune de givre"),
]
NAMES = dict(DIMENSIONS)
REQUIRED = ("meta.json", "points.json", "labels.json", "map_gray.png")


def name(map_id: int) -> str:
    return NAMES.get(int(map_id), f"Carte {map_id}")


def is_ready(map_id: int) -> bool:
    d = map_dir(int(map_id))
    return all((d / f).exists() for f in REQUIRED)


def downloaded() -> list[int]:
    return [mid for mid, _ in DIMENSIONS if is_ready(mid)]


def download(map_id: int, cfg: dict, progress=print) -> None:
    """Télécharge une dimension depuis HoYoLAB (catégories, points, icônes, tuiles de la carte)."""
    from . import hoyolab
    out = map_dir(map_id)
    out.mkdir(parents=True, exist_ok=True)
    lang = cfg.get("lang", "fr-fr")
    progress("Catégories…")
    labels = hoyolab.flatten_labels(hoyolab.fetch_label_tree(map_id, lang))
    (out / "labels.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False), encoding="utf-8")
    progress("Points (coffres, défis…)…")
    points = hoyolab.fetch_points(map_id, lang)
    (out / "points.json").write_text(json.dumps(points, ensure_ascii=False), encoding="utf-8")
    progress("Icônes…")
    wanted = {lid for g in cfg.get("groups", {}).values() for lid in g.get("labels", [])}
    try:
        hoyolab.download_icons(labels, wanted, out / "icons")
    except Exception as e:  # noqa: BLE001 — les icônes manquantes sont remplacées par des pastilles
        log.warning("Icônes de %s : %s", name(map_id), e)
    progress("Carte…")
    info = hoyolab.fetch_map_info(map_id, lang)
    for f in out.glob("features_*.npz"):
        f.unlink()
    meta = hoyolab.download_map(info, "N1", out, progress=lambda s: progress("Carte :" + s.strip()))
    log.info("Dimension %s téléchargée : %d points, carte %s", name(map_id), len(points), meta.get("size"))


class DownloadWorker(QThread):
    progress = pyqtSignal(str)
    done = pyqtSignal(int)
    failed = pyqtSignal(int, str)

    def __init__(self, map_id: int, cfg: dict):
        super().__init__()
        self.map_id = map_id
        self.cfg = cfg

    def run(self):
        try:
            download(self.map_id, self.cfg, progress=lambda s: self.progress.emit(f"{name(self.map_id)} : {s}"))
        except Exception as e:  # noqa: BLE001
            log.error("Téléchargement de %s impossible : %s", name(self.map_id), e)
            self.failed.emit(self.map_id, str(e))
            return
        self.done.emit(self.map_id)
