#!/usr/bin/env python3
"""Enregistre la zone calibrée de la mini-map pendant que tu joues, pour
analyser les échecs de localisation. Images dans data/debug/.

    python tools/record_minimap.py 90      # 90 secondes (défaut : 60)
"""
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gioverlay.capture import ScreenCapture  # noqa: E402
from gioverlay.config import DATA_DIR, load_config  # noqa: E402


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
    reg = load_config().get("minimap_region")
    if not reg:
        print("Calibre d'abord la mini-map dans l'application.")
        return 1
    out = DATA_DIR / "debug"
    out.mkdir(parents=True, exist_ok=True)
    cap = ScreenCapture()
    print("Enregistrement dans 5 s : retourne dans le jeu et déplace-toi (marche, cours, plane, téléporte-toi)…")
    time.sleep(5)
    t0, n = time.time(), 0
    while time.time() - t0 < secs:
        frame, _ = cap.grab(reg["x"], reg["y"], reg["size"])
        cv2.imwrite(str(out / f"f_{int((time.time() - t0) * 1000):06d}.png"), frame)
        n += 1
        time.sleep(0.33)
    print(f"✔ {n} images enregistrées dans {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
