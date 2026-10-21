"""Journal de diagnostic : erreurs, événements importants et captures d'écran quand quelque chose
se passe mal. Le bouton « Créer un rapport » (Paramètres) range tout dans un fichier .zip,
sans jamais y mettre la clé Gemini ni les cookies HoYoLAB.

Fichiers : <données>/logs/journal.log (+ anciens journaux), <données>/logs/erreurs.log et
<données>/logs/captures/*.jpg

Le journal tourne (les plus vieilles lignes disparaissent), mais les ERREURS ne sont jamais perdues :
chacune est recopiée dans erreurs.log avec les lignes qui l'ont précédée, et y reste jusqu'à ce
qu'un rapport soit créé (elles sont alors dans le rapport, et archivées).
"""
from __future__ import annotations

import collections
import json
import logging
import logging.handlers
import platform
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

def _app_version() -> str:
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "?"


APP_VERSION = _app_version()
log = logging.getLogger("gio")
SECRET_KEYS = ("gemini_api_key", "anthropic_api_key", "hoyolab_cookies")
MAX_CAPTURES = 40
MAX_CAPTURE_BYTES = 60 * 1024 * 1024

_dir: Path | None = None
_last_snap: dict[str, float] = {}
_counts = {"errors": 0, "warnings": 0}
_fmt: logging.Formatter | None = None
CONTEXT_LINES = 40                 # lignes du journal recopiées avant chaque erreur
_recent: collections.deque = collections.deque(maxlen=CONTEXT_LINES)
_err_lock = threading.Lock()
_last_err: dict[str, float] = {}   # message -> dernière écriture (évite 1000 fois la même erreur)
_repeats: dict[str, int] = {}
screen_provider = None     # () -> image BGR de l'écran du jeu (ou None si le jeu n'est pas affiché)
captures_enabled = True


class _Counter(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            _counts["errors"] += 1
        elif record.levelno >= logging.WARNING:
            _counts["warnings"] += 1


class _ErrorKeeper(logging.Handler):
    """Recopie chaque erreur (avec son contexte) dans erreurs.log, qui ne tourne jamais."""

    def emit(self, record):
        try:
            line = _fmt.format(record) if _fmt else record.getMessage()
            with _err_lock:
                if record.levelno < logging.ERROR:
                    _recent.append(line)
                    return
                key = f"{record.name}:{record.getMessage()[:200]}"
                now = time.time()
                if now - _last_err.get(key, 0) < 600:      # même erreur il y a moins de 10 min
                    _repeats[key] = _repeats.get(key, 0) + 1
                    _recent.append(line)
                    return
                _last_err[key] = now
                rep = _repeats.pop(key, 0)
                with open(errors_path(), "a", encoding="utf-8") as f:
                    f.write("\n" + "=" * 100 + "\n")
                    if rep:
                        f.write(f"(cette erreur s'est aussi produite {rep} fois depuis la dernière copie)\n")
                    if _recent:
                        f.write("--- contexte (lignes précédentes du journal) ---\n")
                        f.write("\n".join(_recent) + "\n")
                        f.write("--- erreur ---\n")
                    f.write(line + "\n")
                _recent.clear()
        except Exception:  # noqa: BLE001
            pass


def errors_path() -> Path:
    return _dir / "erreurs.log"


def pending_errors() -> int:
    """Nombre d'erreurs gardées qui n'ont pas encore été mises dans un rapport."""
    try:
        return errors_path().read_text(encoding="utf-8", errors="replace").count("=" * 100)
    except (OSError, TypeError):
        return 0


def logs_dir() -> Path | None:
    return _dir


def counts() -> dict:
    return dict(_counts)


def setup(data_dir: Path):
    """À appeler une fois, au tout début du programme."""
    global _dir
    if _dir is not None:
        return
    global _fmt
    _dir = data_dir / "logs"
    (_dir / "captures").mkdir(parents=True, exist_ok=True)
    (_dir / "erreurs-envoyees").mkdir(exist_ok=True)
    _fmt = fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s : %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    fh = logging.handlers.RotatingFileHandler(_dir / "journal.log", maxBytes=2_000_000, backupCount=5,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(_Counter())
    root.addHandler(_ErrorKeeper())
    for noisy in ("urllib3", "httpx", "httpcore", "google_genai", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    def excepthook(et, ev, tb):
        log.critical("Erreur non prévue : %s", "".join(traceback.format_exception(et, ev, tb)).rstrip())
        snapshot("erreur")
        sys.__excepthook__(et, ev, tb)
    sys.excepthook = excepthook

    def thread_hook(args):
        log.critical("Erreur dans le fil %s : %s", getattr(args.thread, "name", "?"),
                     "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip())
    threading.excepthook = thread_hook
    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

        def qt_handler(mode, _ctx, msg):
            if "does not support" in msg or "propagateSizeHints" in msg:
                return
            if mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                log.error("Qt : %s", msg)
            elif mode == QtMsgType.QtWarningMsg:
                log.warning("Qt : %s", msg)
        qInstallMessageHandler(qt_handler)
    except Exception:  # noqa: BLE001
        pass
    log.info("===== Démarrage de Genshin Overlay %s — %s, Python %s =====", APP_VERSION, _system(),
             platform.python_version())


def _system() -> str:
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


# ---------------------------------------------------------------------- captures
def snapshot(reason: str, img=None, min_interval: float = 300.0) -> Path | None:
    """Enregistre une capture (réduite) pour illustrer un problème. Au plus une par raison toutes
    les `min_interval` secondes. Sans image fournie, on capture l'écran du jeu s'il est affiché."""
    if _dir is None or not captures_enabled:
        return None
    now = time.time()
    if now - _last_snap.get(reason, 0) < min_interval:
        return None
    try:
        if img is None:
            img = screen_provider() if screen_provider else None
        if img is None:
            return None
        import cv2
        h, w = img.shape[:2]
        s = min(1.0, 1280 / max(w, h)) if max(w, h) > 400 else 1.0
        if s < 1:
            img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        _last_snap[reason] = now
        path = _dir / "captures" / f"{time.strftime('%Y%m%d-%H%M%S')}_{reason}.jpg"
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 72])
        log.info("Capture enregistrée : %s", path.name)
        _prune()
        return path
    except Exception as e:  # noqa: BLE001
        log.warning("Capture de diagnostic impossible : %s", e)
        return None


def _prune():
    files = sorted((_dir / "captures").glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    total = 0
    for i, f in enumerate(files):
        total += f.stat().st_size
        if i >= MAX_CAPTURES or total > MAX_CAPTURE_BYTES:
            try:
                f.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------- rapport
def report_dir() -> Path:
    return Path.home() / "GenshinOverlay-rapports"


def make_report(cfg: dict, extra: dict | None = None) -> Path:
    """Crée ~/GenshinOverlay-rapports/rapport-AAAAMMJJ-HHMM.zip (sans clé ni cookies)."""
    dest = report_dir()
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"rapport-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    safe = {k: ("(masqué)" if k in SECRET_KEYS and v else v) for k, v in cfg.items()}
    info = {"version": APP_VERSION, "système": _system(), "python": platform.python_version(),
            "créé le": time.strftime("%Y-%m-%d %H:%M:%S"), "compteurs": counts()}
    try:
        from PyQt6.QtCore import QT_VERSION_STR
        from PyQt6.QtGui import QGuiApplication
        info["qt"] = QT_VERSION_STR
        info["écrans"] = [{"nom": s.name(), "taille": [s.geometry().width(), s.geometry().height()],
                           "retina": s.devicePixelRatio()} for s in QGuiApplication.screens()]
    except Exception:  # noqa: BLE001
        pass
    if extra:
        info.update(extra)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("infos.json", json.dumps(info, ensure_ascii=False, indent=2))
        z.writestr("config_sans_secrets.json", json.dumps(safe, ensure_ascii=False, indent=2))
        if _dir is not None:
            for f in sorted(_dir.glob("journal.log*")):
                z.write(f, f"journaux/{f.name}")
            with _err_lock:
                if errors_path().exists():
                    z.write(errors_path(), "journaux/erreurs.log")
            for f in sorted((_dir / "captures").glob("*.jpg")):
                z.write(f, f"captures/{f.name}")
            out = _dir.parent / "overlay.log"     # sortie brute du programme (lanceur)
            if out.exists():
                data = out.read_bytes()[-400_000:]
                z.writestr("journaux/overlay.log", data)
    # Les captures sont maintenant dans le rapport : on les retire du dossier des journaux,
    # et on supprime les anciens rapports (un seul rapport à la fois sur l'ordinateur).
    if _dir is not None:
        # erreurs.log est dans le rapport : on l'archive (jamais supprimé) et on repart de zéro
        with _err_lock:
            if errors_path().exists():
                try:
                    errors_path().rename(_dir / "erreurs-envoyees" / f"erreurs-{path.stem[8:]}.log")
                except OSError:
                    pass
        for f in (_dir / "captures").glob("*.jpg"):
            try:
                f.unlink()
            except OSError:
                pass
    for old in dest.glob("rapport-*.zip"):
        if old != path:
            try:
                old.unlink()
            except OSError:
                pass
    log.info("Rapport créé : %s", path)
    return path
