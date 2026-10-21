#!/usr/bin/env python3
"""Télécharge la carte et les points (coffres, défis…) depuis la map HoYoLAB.

Exemples :
    python fetch_data.py                 # carte + points + icônes (résolution N1)
    python fetch_data.py --points-only   # met à jour uniquement les points (rapide)
    python fetch_data.py --list-labels   # affiche les IDs de catégories disponibles
"""
from __future__ import annotations

import argparse
import json
import sys

from gioverlay import hoyolab
from gioverlay.config import load_config, map_dir


def main() -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map-id", type=int, default=cfg["map_id"], help="2 = Teyvat (défaut)")
    ap.add_argument("--lang", default=cfg["lang"])
    ap.add_argument("--res", default="N1", choices=list(hoyolab.RES_FACTOR),
                    help="N1 (défaut, ~170 Mo de RAM), N2 (léger, moins précis), P0 (énorme)")
    ap.add_argument("--points-only", action="store_true", help="ne retélécharge pas la carte")
    ap.add_argument("--list-labels", action="store_true")
    args = ap.parse_args()

    out = map_dir(args.map_id)
    out.mkdir(parents=True, exist_ok=True)

    print("→ Catégories…")
    labels = hoyolab.flatten_labels(hoyolab.fetch_label_tree(args.map_id, args.lang))
    if args.list_labels:
        for lid, l in sorted(labels.items(), key=lambda kv: (kv[1]["group"], kv[0])):
            if l["count"]:
                print(f"{lid:>5}  {l['group'][:28]:<28}  {l['name']}  ({l['count']})")
        return 0
    (out / "labels.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False), encoding="utf-8")

    print("→ Points…")
    points = hoyolab.fetch_points(args.map_id, args.lang)
    (out / "points.json").write_text(json.dumps(points, ensure_ascii=False), encoding="utf-8")
    print(f"  {len(points)} points")

    print("→ Icônes…")
    wanted = {lid for g in cfg["groups"].values() for lid in g["labels"]}
    hoyolab.download_icons(labels, wanted, out / "icons")

    meta_path = out / "meta.json"
    if args.points_only and meta_path.exists():
        print("Terminé (carte inchangée).")
        return 0

    print("→ Infos de la carte…")
    info = hoyolab.fetch_map_info(args.map_id, args.lang)
    print(f"  {info['name']} — {info['total_size'][0]}×{info['total_size'][1]} px (P0)")
    print(f"→ Téléchargement des tuiles ({args.res})… quelques minutes")
    meta = hoyolab.download_map(info, args.res, out)
    print(f"  carte assemblée : {meta['size'][0]}×{meta['size'][1]} px")

    # L'index de caractéristiques devra être reconstruit pour cette carte.
    for f in out.glob("features_*.npz"):
        f.unlink()
    print("Terminé. Lance maintenant : python run.py")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except hoyolab.HoyolabError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        sys.exit(1)
