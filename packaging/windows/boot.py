"""Genshin Overlay (Windows) : démarrage.

GenshinOverlay.exe lance ce fichier (app\\run.py) avec le Python privé (runtime\\pythonw.exe).
Il choisit le code à utiliser (la dernière mise à jour téléchargée, ou celui livré dans app\\code),
prépare les données du joueur dans %LOCALAPPDATA%\\GenshinOverlay, puis démarre l'overlay.
"""
import ctypes
import hashlib
import os
import re
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent                 # …\GenshinOverlay\app
ROOT = APP.parent
RT = ROOT / "runtime"
SUP = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "GenshinOverlay"
DATA = SUP / "data"
BUNDLED = APP / "code"


def vt(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def message(text):
    ctypes.windll.user32.MessageBoxW(None, text, "Genshin Overlay", 0x10)


def pick_code():
    code = BUNDLED
    bundled_v = (BUNDLED / "VERSION").read_text().strip()
    cur_file = SUP / "code" / "current"
    cur = cur_file.read_text().strip() if cur_file.exists() else ""
    bad_file = SUP / "code" / "bad"
    bad = bad_file.read_text().split() if bad_file.exists() else []
    if cur and cur not in bad and (SUP / "code" / cur / "run.py").exists() and vt(cur) > vt(bundled_v):
        mark = SUP / "code" / f"starting-{cur}"
        n = int(mark.read_text() or 0) if mark.exists() else 0
        if n >= 2:                                   # 2 démarrages ratés : retour au code d'origine
            with open(bad_file, "a") as f:
                f.write(cur + "\n")
            mark.unlink(missing_ok=True)
        else:
            mark.write_text(str(n + 1))
            code = SUP / "code" / cur
    return code


def copy_maps():
    """Carte de Teyvat : copiée une fois dans les données du joueur (sans jamais toucher à sa progression)."""
    src, dst = APP / "maps", DATA / "maps"
    for f in src.rglob("*"):
        t = dst / f.relative_to(src)
        if f.is_file() and not t.exists():
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, t)


def ensure_requirements(code):
    req = code / "requirements.txt"
    want = hashlib.sha256(req.read_bytes()).hexdigest()[:16]
    mark = RT / ".reqhash"
    if mark.exists() and mark.read_text().strip() == want:
        return True
    # Liste des bibliothèques changée (mise à jour) : installation dans une fenêtre visible
    cmd = (f'"{RT / "python.exe"}" -m pip install --no-warn-script-location --disable-pip-version-check '
           f'-r "{req}" & if errorlevel 1 (echo. & echo ERREUR & pause & exit 1)')
    r = subprocess.run(["cmd", "/c", cmd], creationflags=subprocess.CREATE_NEW_CONSOLE)
    if r.returncode != 0:
        message("L'installation des bibliothèques a échoué.\nVérifie ta connexion Internet puis relance Genshin Overlay.")
        return False
    mark.write_text(want)
    return True


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    (SUP / "code").mkdir(parents=True, exist_ok=True)
    log = open(DATA / "overlay.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    copy_maps()
    code = pick_code()
    if not ensure_requirements(code):
        return 1
    started = (code / "VERSION").read_text().strip()
    os.environ.update(GIO_DATA_DIR=str(DATA), GIO_SUP=str(SUP), GIO_APP_RES=str(BUNDLED),
                      GIO_CODE_DIR=str(code), PYTHONDONTWRITEBYTECODE="1")
    sys.dont_write_bytecode = True
    os.chdir(code)
    sys.path.insert(0, str(code))
    sys.argv = [str(code / "run.py")]
    try:
        runpy.run_path(str(code / "run.py"), run_name="__main__")
    except SystemExit:
        pass
    # « Redémarrer pour mettre à jour » : une version plus récente a été installée → on relance
    cur_file = SUP / "code" / "current"
    cur = cur_file.read_text().strip() if cur_file.exists() else ""
    if cur and vt(cur) > vt(started) and (SUP / "code" / cur / "run.py").exists() \
            and (SUP / "code" / ".restart").exists():
        (SUP / "code" / ".restart").unlink(missing_ok=True)
        subprocess.Popen([str(ROOT / "GenshinOverlay.exe")], close_fds=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
