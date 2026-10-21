#!/usr/bin/env python3
"""Construit GenshinOverlay-Windows-<version>.zip (sans aucune donnée personnelle).

    python packaging/windows/build_zip.py CODE_DIR MAP_DATA_DIR OUT_DIR

CODE_DIR : racine du dépôt ; MAP_DATA_DIR : un dossier « maps » (créé par fetch_data.py).
GenshinOverlay.exe est compilé depuis launcher.c / launcher.rc / app.manifest (MSVC ou clang-cl).
"""
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE_FILES = ("run.py", "requirements.txt", "VERSION", "channel.json")


def main(code: Path, maps: Path, out: Path):
    ver = (code / "VERSION").read_text().strip()
    dist = out / "dist" / "GenshinOverlay"
    shutil.rmtree(out / "dist", ignore_errors=True)
    app = dist / "app"
    (app / "code").mkdir(parents=True)
    shutil.copy2(HERE / "GenshinOverlay.exe", dist)
    shutil.copy2(HERE / "LISEZ-MOI.txt", dist)
    shutil.copy2(HERE / "boot.py", app / "run.py")
    shutil.copy2(HERE / "setup.ps1", app)
    for f in CODE_FILES:
        shutil.copy2(code / f, app / "code" / f)
    shutil.copytree(code / "gioverlay", app / "code" / "gioverlay",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(maps, app / "maps", ignore=shutil.ignore_patterns("progress.json", "tiles"))
    zp = out / f"GenshinOverlay-Windows-{ver}.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(dist.parent.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(dist.parent))
    print(zp)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
