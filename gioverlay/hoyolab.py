"""Récupération des données de la carte interactive HoYoLAB.

Endpoints publics utilisés par act.hoyolab.com/ys/app/interactive-map :
  - v3/map/info        -> taille de la carte, origine, version des tuiles
  - v2/map/label/tree  -> catégories (coffres, défis…)
  - v3/map/point/list  -> tous les points
  - tuiles : act-webstatic.hoyoverse.com/map_manage/map/<id>/<version>/<x>_<y>_<res>.webp

Coordonnées : pixel_carte_P0 = origin + (x_pos, y_pos). Les résolutions
N1 / N2 sont P0 divisé par 2 / 4.
"""
from __future__ import annotations

import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import requests
from PIL import Image

API = "https://sg-public-api-static.hoyolab.com/common/map_user/ys_obc"
TILE_URL = "https://act-webstatic.hoyoverse.com/map_manage/map/{map_id}/{version}/{x}_{y}_{res}.webp"
TILE_PX = 256
RES_FACTOR = {"P0": 1, "N1": 2, "N2": 4}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://act.hoyolab.com/",
    "Origin": "https://act.hoyolab.com",
    "Accept": "application/json, text/plain, */*",
}

_session = requests.Session()
_session.headers.update(HEADERS)


class HoyolabError(RuntimeError):
    pass


def _get_json(path: str, map_id: int, lang: str) -> dict:
    params = {"app_sn": "ys_obc", "lang": lang, "map_id": map_id}
    last = None
    for attempt in range(4):
        try:
            r = _session.get(f"{API}/{path}", params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            if data.get("retcode") != 0:
                raise HoyolabError(f"{path}: retcode={data.get('retcode')} {data.get('message')}")
            return data["data"]
        except (requests.RequestException, ValueError, HoyolabError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise HoyolabError(f"Échec de {path} : {last}")


def fetch_map_info(map_id: int, lang: str) -> dict:
    info = _get_json("v3/map/info", map_id, lang)["info"]
    d = info.get("detail_v2") or {}
    if not d or not d.get("total_size"):
        raise HoyolabError(f"La carte {map_id} n'a pas de tuiles (detail_v2 vide).")
    legacy = None
    try:  # ancien format (grandes images de 2048 px), utilisé en secours
        legacy = json.loads(info.get("detail") or "null")
    except ValueError:
        pass
    return {
        "map_id": map_id,
        "name": info.get("name", ""),
        "total_size": d["total_size"],
        "origin": d["origin"],
        "padding": d.get("padding", [0, 0]),
        "map_version": d["map_version"],
        "legacy": legacy,
    }


def fetch_label_tree(map_id: int, lang: str) -> list[dict]:
    return _get_json("v2/map/label/tree", map_id, lang)["tree"]


def flatten_labels(tree: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}

    def walk(nodes, parent_name):
        for n in nodes:
            if n.get("children"):
                walk(n["children"], n["name"])
            else:
                out[int(n["id"])] = {
                    "name": n["name"],
                    "group": parent_name,
                    "icon": n.get("icon", ""),
                    "count": n.get("point_count", 0),
                }

    for top in tree:
        walk(top.get("children", []), top["name"])
    return out


def fetch_points(map_id: int, lang: str) -> list[dict]:
    raw = _get_json("v3/map/point/list", map_id, lang)["point_list"]
    pts = []
    for p in raw:
        if p.get("display_state", 1) != 1:
            continue
        pts.append({
            "id": int(p["id"]),
            "label_id": int(p["label_id"]),
            "x": float(p["x_pos"]),
            "y": float(p["y_pos"]),
            "area_id": p.get("area_id"),
            # Point situé dans un étage / souterrain : la mini-map ne permet pas
            # de savoir à quel niveau on se trouve.
            "layered": bool(p.get("point_group")) or bool(p.get("z_level")),
        })
    return pts


def _download(url: str) -> bytes | None:
    for attempt in range(3):
        try:
            r = _session.get(url, timeout=30)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            time.sleep(1 + attempt)
    return None


def download_map(info: dict, res: str, out_dir: Path, workers: int = 16, progress=print) -> dict:
    """Télécharge et assemble les tuiles. Écrit map_gray.png + preview.jpg + meta.json."""
    factor = RES_FACTOR[res]
    W0, H0 = info["total_size"]
    nx, ny = W0 // (TILE_PX * factor), H0 // (TILE_PX * factor)
    W, H = nx * TILE_PX, ny * TILE_PX
    gray = np.zeros((H, W), np.uint8)
    preview_scale = min(1.0, 4096 / W)
    pw, ph = int(W * preview_scale), int(H * preview_scale)
    preview = Image.new("RGB", (pw, ph), (12, 20, 32))

    jobs = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for x in range(nx):
            for y in range(ny):
                url = TILE_URL.format(map_id=info["map_id"], version=info["map_version"], x=x, y=y, res=res)
                jobs[ex.submit(_download, url)] = (x, y)
        done = missing = 0
        for fut in as_completed(jobs):
            x, y = jobs[fut]
            data = fut.result()
            done += 1
            if data is None:
                missing += 1
            else:
                tile = Image.open(io.BytesIO(data)).convert("RGBA")
                bg = Image.new("RGBA", tile.size, (0, 0, 0, 255))
                tile = Image.alpha_composite(bg, tile).convert("RGB")
                g = np.asarray(tile.convert("L"))
                gray[y * TILE_PX:y * TILE_PX + g.shape[0], x * TILE_PX:x * TILE_PX + g.shape[1]] = g
                if preview_scale < 1:
                    s = max(1, int(round(TILE_PX * preview_scale)))
                    preview.paste(tile.resize((s, s)), (int(x * TILE_PX * preview_scale), int(y * TILE_PX * preview_scale)))
                else:
                    preview.paste(tile, (x * TILE_PX, y * TILE_PX))
            if done % 50 == 0 or done == len(jobs):
                progress(f"  tuiles {done}/{len(jobs)} ({missing} vides)")

    if missing == len(jobs):
        if info.get("legacy"):
            progress("  tuiles introuvables → ancien format de carte (peut manquer les régions récentes)")
            return download_map_legacy(info, factor, out_dir, workers, progress)
        raise HoyolabError("Aucune tuile téléchargée : l'URL des tuiles a peut-être changé.")

    out_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gray).save(out_dir / "map_gray.png", optimize=False, compress_level=3)
    preview.save(out_dir / "preview.jpg", quality=88)
    info = {k: v for k, v in info.items() if k != "legacy"}
    meta = dict(info, res=res, scale=1.0 / factor, size=[W, H], preview_scale=preview_scale)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def download_map_legacy(info: dict, factor: int, out_dir: Path, workers=8, progress=print) -> dict:
    """Secours : carte « detail » (images de 2048 px, origine différente)."""
    lg = info["legacy"]
    slices = lg["slices"]
    W0, H0 = lg["total_size"]
    W, H = W0 // factor, H0 // factor
    gray = np.zeros((H, W), np.uint8)
    step = 2048 // factor
    jobs = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for row, line in enumerate(slices):
            for col, sl in enumerate(line):
                jobs[ex.submit(_download, sl["url"])] = (col, row)
        for n, fut in enumerate(as_completed(jobs), 1):
            col, row = jobs[fut]
            data = fut.result()
            if data:
                im = Image.open(io.BytesIO(data)).convert("RGBA")
                bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
                im = Image.alpha_composite(bg, im).convert("L")
                im = im.resize((im.width // factor, im.height // factor), Image.LANCZOS)
                g = np.asarray(im)
                y0, x0 = row * step, col * step
                h, w = min(g.shape[0], H - y0), min(g.shape[1], W - x0)
                gray[y0:y0 + h, x0:x0 + w] = g[:h, :w]
            progress(f"  images {n}/{len(jobs)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gray).save(out_dir / "map_gray.png", compress_level=3)
    ps = min(1.0, 4096 / W)
    Image.fromarray(gray).convert("RGB").resize((int(W * ps), int(H * ps))).save(out_dir / "preview.jpg", quality=88)
    meta = {k: v for k, v in info.items() if k != "legacy"}
    meta.update(total_size=lg["total_size"], origin=lg["origin"], padding=lg.get("padding", [0, 0]),
                res=f"legacy/{factor}", scale=1.0 / factor, size=[W, H], preview_scale=ps)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def download_icons(labels: dict[int, dict], label_ids, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [(lid, labels[lid]["icon"]) for lid in label_ids if lid in labels and labels[lid].get("icon")]

    def one(item):
        lid, url = item
        path = out_dir / f"{lid}.png"
        if path.exists():
            return
        data = _download(url)
        if data:
            Image.open(io.BytesIO(data)).convert("RGBA").save(path)

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, todo))
