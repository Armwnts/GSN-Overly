#!/usr/bin/env python3
"""Dessine les points activés sur l'aperçu de la carte, pour vérifier qu'ils
tombent au bon endroit. Résultat : data/maps/<id>/preview_points.jpg

    python tools/preview_points.py
    python tools/preview_points.py --crop 0.45 0.55 0.15   # zoom (centre x, centre y, largeur, en fraction)
"""
import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gioverlay.config import load_config, map_dir  # noqa: E402
from gioverlay.mapdata import MapData  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crop", nargs=3, type=float, metavar=("CX", "CY", "W"))
    args = ap.parse_args()
    cfg = load_config()
    data = MapData(map_dir(cfg["map_id"]), cfg.get("offset_map_px", (0, 0)))
    img = Image.open(data.folder / "preview.jpg").convert("RGB")
    k = data.meta["preview_scale"]
    d = ImageDraw.Draw(img)
    n = 0
    for g in cfg["groups"].values():
        if not g.get("enabled"):
            continue
        for x, y, lid in zip(data.xy[:, 0], data.xy[:, 1], data.label_ids):
            if lid in g["labels"]:
                d.ellipse([x * k - 2, y * k - 2, x * k + 2, y * k + 2], fill=g["color"])
                n += 1
    if args.crop:
        cx, cy, w = args.crop
        W, H = img.size
        half = w * W / 2
        img = img.crop((int(cx * W - half), int(cy * H - half), int(cx * W + half), int(cy * H + half)))
        img = img.resize((1600, 1600), Image.LANCZOS)
    out = data.folder / "preview_points.jpg"
    img.save(out, quality=90)
    print(f"{n} points dessinés → {out}")


if __name__ == "__main__":
    main()
