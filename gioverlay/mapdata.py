"""Points de la carte, conversion de coordonnées et progression (coffres ouverts)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class MapData:
    def __init__(self, folder: Path, offset=(0.0, 0.0)):
        self.folder = folder
        self.meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        self.labels = {int(k): v for k, v in
                       json.loads((folder / "labels.json").read_text(encoding="utf-8")).items()}
        pts = json.loads((folder / "points.json").read_text(encoding="utf-8"))
        self.scale = float(self.meta["scale"])
        ox, oy = self.meta["origin"]
        self.ids = np.array([p["id"] for p in pts], np.int64)
        self.label_ids = np.array([p["label_id"] for p in pts], np.int32)
        self.layered = np.array([p.get("layered", False) for p in pts], bool)
        raw = np.array([[p["x"], p["y"]] for p in pts], np.float64).reshape(-1, 2)
        self.xy = ((raw + [ox, oy]) * self.scale + np.asarray(offset)).astype(np.float32)
        # Coordonnées « P0 » (pleine résolution de la carte HoYoLAB) pour la carte interactive.
        self.p0 = (raw + [ox, oy]).astype(np.float32)
        self.progress = Progress(folder / "progress.json")

    @property
    def map_path(self) -> Path:
        return self.folder / "map_gray.png"

    def icon_path(self, label_id: int) -> Path:
        return self.folder / "icons" / f"{label_id}.png"

    def p0_to_map(self, d: float) -> float:
        return d * self.scale

    def query_circle(self, cx: float, cy: float, r: float, label_ids: set[int], include_layered=True):
        """Indices des points des labels donnés dans le cercle (cx, cy, r) en px carte."""
        if not label_ids or len(self.xy) == 0:
            return np.empty(0, np.int64)
        d2 = (self.xy[:, 0] - cx) ** 2 + (self.xy[:, 1] - cy) ** 2
        m = (d2 <= r * r) & np.isin(self.label_ids, list(label_ids))
        if not include_layered:
            m &= ~self.layered
        return np.nonzero(m)[0]


class Progress:
    """Points marqués comme faits, sauvegardés dans progress.json."""

    def __init__(self, path: Path):
        self.path = path
        self.done: set[int] = set()
        self.history: list[list[int]] = []  # chaque entrée = un marquage (un ou plusieurs points)
        if path.exists():
            try:
                self.done = set(json.loads(path.read_text(encoding="utf-8")).get("done", []))
            except (json.JSONDecodeError, OSError):
                pass

    def mark(self, pid: int) -> None:
        self.mark_many([pid])

    def mark_many(self, pids) -> int:
        new = [int(p) for p in pids if int(p) not in self.done]
        if new:
            self.done.update(new)
            self.history.append(new)
            self.save()
        return len(new)

    def unmark(self, pid: int) -> bool:
        """Décoche un point (depuis la carte interactive)."""
        pid = int(pid)
        if pid not in self.done:
            return False
        self.done.discard(pid)
        for batch in self.history:
            if pid in batch:
                batch.remove(pid)
        self.history = [b for b in self.history if b]
        self.save()
        return True

    def undo(self) -> int:
        """Annule le dernier marquage ; renvoie le nombre de points rétablis."""
        while self.history:
            batch = [p for p in self.history.pop() if p in self.done]
            if batch:
                self.done.difference_update(batch)
                self.save()
                return len(batch)
        return 0

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"done": sorted(self.done)}), encoding="utf-8")
        tmp.replace(self.path)
