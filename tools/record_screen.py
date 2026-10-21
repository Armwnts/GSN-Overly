#!/usr/bin/env python3
"""Enregistre l'écran du jeu (pour analyser la carte plein écran). Images dans data/debug_map/.

    python tools/record_screen.py 45
"""
import sys
import time
from pathlib import Path

import cv2
import mss
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gioverlay.config import DATA_DIR, load_config  # noqa: E402


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 45
    reg = load_config().get("minimap_region") or {"x": 0, "y": 0, "size": 1}
    cx, cy = reg["x"] + reg["size"] / 2, reg["y"] + reg["size"] / 2
    out = DATA_DIR / "debug_map"
    out.mkdir(parents=True, exist_ok=True)
    with mss.mss() as sct:
        mons = sct.monitors[1:]
        mon = next((m for m in mons if m["left"] <= cx < m["left"] + m["width"]
                    and m["top"] <= cy < m["top"] + m["height"]), mons[0])
        print("Enregistrement dans 5 s : ouvre la carte (M), déplace-la et zoome/dézoome lentement…")
        time.sleep(5)
        t0, n = time.time(), 0
        while time.time() - t0 < secs:
            img = np.asarray(sct.grab(mon))[:, :, :3]
            cv2.imwrite(str(out / f"s_{int((time.time() - t0) * 1000):06d}.jpg"), img,
                        [cv2.IMWRITE_JPEG_QUALITY, 88])
            n += 1
            time.sleep(0.7)
    print(f"✔ {n} images enregistrées dans {out}")


if __name__ == "__main__":
    main()
