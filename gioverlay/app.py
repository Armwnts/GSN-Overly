"""Point d'entrée : relie capture, localisation, overlay et panneau."""
from __future__ import annotations

import os
import sys
import threading
import time
import logging

import cv2
import numpy as np
from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import macos
from .calibration import CalibrationWindow
from .capture import CaptureError, ScreenCapture
from .config import load_config, map_dir, save_config
from .locator import FeatureIndex, LocateResult, Locator
from .mapdata import MapData
from .mapview import MapView, MapViewLocator
from .overlay import MARGIN, MapOverlay, Marker, Overlay, QuestCard
from .panel import Panel

EDGE_RANGE = 2.5  # on signale au bord les points jusqu'à 2,5x le rayon de la mini-map


class TrackerWorker(QThread):
    """Fil de calcul : construit l'index de la carte puis localise chaque image."""
    progress = pyqtSignal(str)
    ready = pyqtSignal(int)
    located = pyqtSignal(object, float)  # LocateResult | None, durée en ms
    map_located = pyqtSignal(object, object, float)  # MapView | None, (w, h) capture, durée
    failed = pyqtSignal(str)
    dimension_found = pyqtSignal(int)   # la mini-map correspond à une autre dimension téléchargée

    def __init__(self, map_path, cfg, probe_ids=()):
        super().__init__()
        self.map_path = map_path
        self.probe_ids = list(probe_ids)
        self._probes: dict = {}
        self._lost = 0
        self._next_probe = 0.0
        self._probe_i = 0
        self._hit = (None, 0)
        self.cfg = cfg
        self._jobs: dict[str, tuple] = {}   # une seule image en attente par type (la plus récente)
        self._cv = threading.Condition()
        self.px_per_pt = None
        self.mapview: MapViewLocator | None = None
        self._stop = False
        self._reset = False
        self.locator: Locator | None = None

    def submit(self, frame, mask_pts, mask_r, size_pt):
        with self._cv:
            self._jobs["mini"] = (frame, mask_pts, mask_r, size_pt)
            self._cv.notify()

    def submit_map(self, screen):
        with self._cv:
            self._jobs["map"] = (screen,)
            self._cv.notify()

    def request_reset(self):
        self._reset = True

    def stop(self):
        self._stop = True

    def run(self):
        try:
            last_pct = [-1]

            def prog(i, n):
                pct = int(100 * i / n)
                if pct != last_pct[0] and pct % 2 == 0:
                    last_pct[0] = pct
                    self.progress.emit(f"Analyse de la carte (une seule fois) : {pct} %")

            self.progress.emit("Chargement de la carte…")
            index = FeatureIndex.load_or_build(self.map_path, progress=prog)
            self.progress.emit("Préparation de la recherche…")
            _ = index.flann  # construit l'arbre de recherche maintenant
            gray = cv2.imread(str(self.map_path), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                raise RuntimeError(f"Impossible de lire {self.map_path}")
            self.locator = Locator(index, gray, track_radius_factor=self.cfg["track_radius_factor"])
            del gray
            self.mapview = MapViewLocator(index, self.locator.map)
            self.ready.emit(len(index))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")
            return
        px_per_pt = None
        while not self._stop:
            with self._cv:
                if not self._jobs:
                    self._cv.wait(timeout=0.2)
                jobs, self._jobs = self._jobs, {}
            if "map" in jobs:
                (screen,) = jobs["map"]
                t = time.perf_counter()
                try:
                    v = self.mapview.locate(screen)
                except Exception as e:
                    print(f"[carte] {e}")
                    v = None
                self.map_located.emit(v, (screen.shape[1], screen.shape[0]), (time.perf_counter() - t) * 1000)
            if "mini" not in jobs:
                continue
            frame, mpts, mr, size_pt = jobs["mini"]
            k = frame.shape[1] / size_pt
            if px_per_pt is None or abs(k / px_per_pt - 1) > 0.02:
                # Échelles mémorisées (en px carte par point d'écran) -> px de l'image capturée
                px_per_pt = k
                self.locator.known_scales = [s / k for s in self.cfg.get("known_scales_pt", [])]
            self.px_per_pt = px_per_pt
            if self._reset:
                self.locator.reset()
                self._reset = False
            t = time.perf_counter()
            try:
                r = self.locator.locate(frame, mpts, mr)
            except Exception as e:  # une image corrompue ne doit pas tout arrêter
                print(f"[locate] {e}")
                r = None
            self.located.emit(r, (time.perf_counter() - t) * 1000)
            if r is not None or not self.locator.minimap_visible:
                self._lost, self._hit = 0, (None, 0)
            else:
                self._lost += 1
                if self.probe_ids and self._lost >= 10 and time.monotonic() >= self._next_probe:
                    self._probe(frame, mpts, mr)

    def _probe(self, frame, mpts, mr):
        """Joueur introuvable alors que la mini-map est visible : est-il dans une autre dimension ?"""
        mid = self.probe_ids[self._probe_i % len(self.probe_ids)]
        self._probe_i += 1
        self._next_probe = time.monotonic() + 0.6
        try:
            loc = self._probes.get(mid)
            if loc is None:
                from .config import map_dir
                path = map_dir(mid) / "map_gray.png"
                idx = FeatureIndex.load_or_build(path)
                _ = idx.flann
                gray = None
                import json
                w, h = json.loads((path.parent / "meta.json").read_text(encoding="utf-8")).get("size", [0, 0])
                if w * h <= 30_000_000:      # petites cartes : vérification par l'image (Teyvat : trop lourd)
                    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                loc = self._probes[mid] = Locator(idx, gray)
            res = loc.probe(frame, mpts, mr)
        except Exception as e:  # noqa: BLE001
            print(f"[dimension {mid}] {e}")
            self.probe_ids = [p for p in self.probe_ids if p != mid]
            return
        if res is None:
            return
        n = self._hit[1] + 1 if self._hit[0] == mid else 1
        self._hit = (mid, n)
        if n >= 2:
            self._lost, self._hit = 0, (None, 0)
            self.dimension_found.emit(mid)


class QuestWorker(QThread):
    """Envoie la capture à Claude sans bloquer l'interface."""
    status = pyqtSignal(str)
    answered = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, provider, api_key, model, screen, question):
        super().__init__()
        self.provider, self.api_key, self.model = provider, api_key, model
        self.screen, self.question = screen, question

    def run(self):
        from .quest import QuestError, QuestHelper
        try:
            if self.provider == "gemini":
                from .quest_gemini import GeminiQuestHelper
                helper = GeminiQuestHelper(self.api_key, self.model or None)
            else:
                helper = QuestHelper(self.api_key, self.model)
            self.answered.emit(helper.ask(self.screen, self.question, self.status.emit))
        except QuestError as e:
            logging.getLogger("gio.quete").warning("Aide de quête : %s", e)
            self.failed.emit(str(e))
        except Exception as e:  # filet de sécurité : jamais de plantage de l'app
            logging.getLogger("gio.quete").exception("Aide de quête : erreur imprévue")
            self.failed.emit(f"{type(e).__name__} : {e}")


log = logging.getLogger("gio.app")


class HotkeyBridge(QObject):
    fired = pyqtSignal(str)  # permet d'appeler Qt depuis le fil de pynput


class Controller(QObject):
    notes_updated = pyqtSignal(object)   # dict des notes HoYoLAB, ou message d'erreur (str)
    checkin_done = pyqtSignal(str)
    update_status = pyqtSignal(str)      # état des mises à jour (affiché dans les Paramètres)

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.cfg = load_config()
        from . import diag
        from .config import DATA_DIR
        diag.setup(DATA_DIR)
        diag.screen_provider = self._diag_screen
        diag.captures_enabled = bool(self.cfg.get("diag_captures", True))
        self._minimap_seen_at = 0.0
        self._lost_since = None
        self._capture_errors = 0
        self._black_logged = False
        from . import theme
        theme.install(self.cfg.get("accent_color"))
        from . import dimensions
        if not dimensions.is_ready(self.cfg.get("map_id", 2)):
            self.cfg["map_id"] = dimensions.TEYVAT
        self.dim_worker = None
        self.data = self._load_map_data(self.cfg["map_id"])
        self.capture = ScreenCapture()
        self.last: LocateResult | None = None
        self.cap_scale = 1.0
        self.mm_px = 0           # taille de l'image capturée (px)
        self.ready = False
        self.overlay_on = True
        self.last_ms = 0.0
        self._scales_saved_at = 0.0
        self.mask_pts: list[tuple[float, float]] = []
        from .heading import HeadingFilter
        self.heading_filter = HeadingFilter()
        self.heading = (None, None)

        self.overlay = Overlay(self.data.icon_path, self.cfg["icon_size"])
        # Carte plein écran du jeu
        self.map_overlay = MapOverlay(self.data.icon_path)
        self.want_map = False      # mini-map invisible : la carte est peut-être ouverte
        self.last_view: MapView | None = None
        self.view_size = (0, 0)    # taille de la capture d'écran (px)
        self.screen_scale = 1.0    # px capturés par point
        self.map_misses = 0
        self._tick_n = 0
        # Aide de quête par IA
        self.quest_card = QuestCard()
        self._quest_workers: list[QuestWorker] = []
        self.map_window = None  # carte interactive, créée au premier clic sur le titre du panneau
        self._quest_req = 0
        self.panel = Panel(self.cfg, self._counts())
        p = self.panel
        p.calibrate.connect(self.start_calibration)
        p.mark_nearest.connect(self.mark_nearest)
        p.undo_mark.connect(self.undo_mark)
        p.toggle_overlay.connect(self.toggle_overlay)
        p.relocalize.connect(self.relocalize)
        p.reset_progress.connect(self.reset_progress)
        p.mark_view.connect(self.mark_view)
        p.quest_help.connect(self.quest_help)
        p.open_map.connect(self.open_map_window)
        p.quest_close.connect(self.close_quest)
        p.api_key_changed.connect(self._set_api_key)
        p.groups_changed.connect(self._on_groups_changed)
        p.skip_toggled.connect(self.set_auto_skip)
        p.restart_update.connect(self.restart_for_update)
        p.show()

        p.dimension_chosen.connect(lambda mid: self.switch_dimension(mid, manual=True))
        self.worker = None
        self._start_worker()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(1000 / max(1, self.cfg["fps"])))

        self.hotkeys = None
        if self.cfg.get("hotkeys_enabled", True):
            self._setup_hotkeys()

        # Passer les dialogues automatiquement
        from .skipper import DialogueSkipper
        self.game_pid = None          # application du jeu (au premier plan quand la mini-map est vue)
        self._game_pid_at = 0.0
        self._pid_votes = 0
        self.skipper = DialogueSkipper(self.capture, self._game_screen_point, self._hud_frame,
                                       lambda: self.game_pid)
        self.skipper.status.connect(self.panel.flash)
        self.skipper.finished.connect(lambda n: self.panel.flash(f"✓ Dialogue terminé ({n} × F)."))
        if self.cfg.get("auto_skip"):
            QTimer.singleShot(1500, lambda: self.set_auto_skip(True))

        # Notes en temps réel HoYoLAB (résine…) et connexion quotidienne
        self.notes = None
        self._bg: set = set()
        self._resin_alerted = False
        self._notes_busy = False
        self.notes_timer = QTimer(self, interval=8 * 60 * 1000, timeout=self.refresh_notes)
        self.notes_timer.start()
        self.notes_render_timer = QTimer(self, interval=30 * 1000, timeout=self.render_notes)
        self.notes_render_timer.start()
        QTimer.singleShot(3000, self.refresh_notes)
        # Mises à jour automatiques
        from .updater import Updater
        self.updater = Updater()
        self.update_ready = None
        self.update_text = "Pas encore vérifié."
        self._update_busy = False
        if self.updater.configured:
            self.update_timer = QTimer(self, interval=6 * 3600 * 1000, timeout=self.check_update)
            self.update_timer.start()
            QTimer.singleShot(60000, self.check_update)
        else:
            self.update_text = "Mises à jour automatiques indisponibles dans cette installation."
        self.events_timer = QTimer(self, interval=2 * 3600 * 1000, timeout=self.check_events)
        self.events_timer.start()
        QTimer.singleShot(45000, self.check_events)
        self.checkin_timer = QTimer(self, interval=3 * 3600 * 1000, timeout=self.do_checkin)
        self.checkin_timer.start()
        QTimer.singleShot(20000, self.do_checkin)
        # Widget dans la barre des menus (résine, argent du domaine, événements)
        self.calendar = None
        self.menubar = None
        self.desk = None
        self.checkin_text = None
        if self.cfg.get("menubar_widget", True):
            QTimer.singleShot(500, lambda: self.set_menubar(True))
        if self.cfg.get("desk_widget", True):
            QTimer.singleShot(700, lambda: self.set_desk_widget(True))
        # Relancer l'app (Launchpad, Applications…) alors qu'elle tourne en arrière-plan : on réaffiche le panneau
        app.applicationStateChanged.connect(self._app_state)
        self.errors_timer = QTimer(self, interval=10 * 60 * 1000, timeout=self.check_errors)
        self.errors_timer.start()
        QTimer.singleShot(90000, self.check_errors)

        if self.cfg.get("minimap_region"):
            self._apply_region(self.cfg["minimap_region"])
        app.aboutToQuit.connect(self.shutdown)

    # --- dimensions (Teyvat, Enkanomiya, le Gouffre…) ----------------------------
    def _load_map_data(self, map_id):
        from . import dimensions
        off = self.cfg.get("offset_map_px", (0, 0)) if map_id == dimensions.TEYVAT else (0, 0)
        return MapData(map_dir(map_id), off)

    def _start_worker(self):
        from . import dimensions
        probes = []
        if self.cfg.get("auto_dimension", True):
            probes = [m for m in dimensions.downloaded() if m != self.cfg["map_id"]]
        self.worker = TrackerWorker(self.data.map_path, self.cfg, probes)
        p = self.panel
        self.worker.progress.connect(lambda s: p.set_status(s))
        self.worker.ready.connect(self._on_ready)
        self.worker.located.connect(self._on_located)
        self.worker.map_located.connect(self._on_map_located)
        self.worker.failed.connect(self._on_worker_failed)
        self.worker.dimension_found.connect(lambda mid: self.switch_dimension(mid, manual=False))
        self.worker.start()

    def switch_dimension(self, map_id: int, manual: bool = True):
        from . import dimensions
        map_id = int(map_id)
        if map_id == self.cfg["map_id"]:
            return
        if not dimensions.is_ready(map_id):
            if not manual:
                return
            if self.dim_worker is not None and self.dim_worker.isRunning():
                self.panel.flash("Un téléchargement est déjà en cours…")
                return
            self.panel.set_status(f"<span style='color:#f5c542'>⬇ Téléchargement de "
                                  f"{dimensions.name(map_id)} depuis HoYoLAB…</span>")
            w = self.dim_worker = dimensions.DownloadWorker(map_id, self.cfg)
            w.progress.connect(lambda t: self.panel.set_status(f"<span style='color:#f5c542'>⬇ {t}</span>"))
            w.done.connect(lambda mid: self._do_switch(mid, "téléchargée"))
            w.failed.connect(lambda mid, err: (self.panel.set_dimension(self.cfg["map_id"]),
                                               self.panel.set_status(
                                                   f"<span style='color:#ff6b6b'>Téléchargement de "
                                                   f"{dimensions.name(mid)} impossible.</span><br>{err}")))
            w.start()
            return
        self._do_switch(map_id, "choisie" if manual else "détectée")

    def _do_switch(self, map_id: int, how: str):
        from . import dimensions
        log.info("Dimension %s : %s", how, dimensions.name(map_id))
        old = self.worker
        if old is not None:
            old.blockSignals(True)
            old.stop()
            old.wait(5000)
        self.cfg["map_id"] = map_id
        save_config(self.cfg)
        self.data = self._load_map_data(map_id)
        self.overlay._icon_path_for = self.data.icon_path
        self.map_overlay._icon_path_for = self.data.icon_path
        self.ready = False
        self.last = None
        self.last_view = None
        self.want_map = False
        self.map_overlay.hide()
        self.overlay.set_markers([], "idle")
        if self.map_window is not None:      # la grande carte sera recréée pour cette dimension
            was_open = self.map_window.isVisible()
            self.map_window.close()
            self.map_window.deleteLater()
            self.map_window = None
            if was_open:
                QTimer.singleShot(300, self.open_map_window)
        self.panel.set_dimension(map_id)
        self.panel.update_counts(self._counts())
        self.panel.flash(f"🌐 Dimension {how} : {dimensions.name(map_id)}")
        self._start_worker()

    # --- mise en place -----------------------------------------------------------
    def _setup_hotkeys(self):
        self.bridge = HotkeyBridge()
        actions = {"toggle_overlay": self.toggle_overlay, "mark_nearest": self.mark_nearest,
                   "undo_mark": self.undo_mark, "relocalize": self.relocalize, "mark_view": self.mark_view,
                   "quest_help": self.quest_help}
        self.bridge.fired.connect(lambda name: actions[name]())
        from .macos import GlobalHotkeys
        bindings = {spec: (lambda n=name: self.bridge.fired.emit(n))
                    for name, spec in self.cfg["hotkeys"].items() if name in actions}
        try:
            self.hotkeys = GlobalHotkeys(bindings)
            msg = self.hotkeys.start()
            log.info("Raccourcis : %s", self.hotkeys.status())
            if msg:
                log.warning("Raccourcis : %s", msg)
                self.panel.flash(msg)
        except Exception as e:
            self.panel.flash(f"Raccourcis indisponibles : {e}")

    # --- passer les dialogues ------------------------------------------------------------
    def _game_screen_point(self):
        reg = self.cfg.get("minimap_region")
        if reg:
            return reg["x"] + reg["size"] / 2, reg["y"] + reg["size"] / 2
        from PyQt6.QtGui import QGuiApplication
        g = QGuiApplication.primaryScreen().geometry()
        return g.center().x(), g.center().y()

    def _hud_frame(self):
        """Image de la mini-map calibrée (pour savoir si l'interface normale du jeu est revenue)."""
        reg = self.cfg.get("minimap_region")
        if not reg:
            return None
        frame, _ = self.capture.grab(reg["x"], reg["y"], reg["size"])
        return frame

    def set_auto_skip(self, on: bool):
        from . import dialogue
        self.cfg["auto_skip"] = on
        save_config(self.cfg)
        self.skipper.set_enabled(on)
        if not on:
            self.panel.flash("Passer les dialogues : désactivé.")
            return
        if not dialogue.can_send_keys():
            dialogue.request_key_permission()
            self.panel.flash("⚠ Pour appuyer sur F à ta place, autorise l'app dans Réglages Système → "
                             "Confidentialité et sécurité → Accessibilité, puis relance-la.")
        else:
            self.panel.flash("⏩ Passer les dialogues : activé. Parle à un PNJ, je m'occupe du reste.")

    def restart_hotkeys(self):
        if self.hotkeys is not None:
            try:
                self.hotkeys.stop()
            except Exception as e:  # noqa: BLE001
                print(f"[raccourcis] arrêt : {e}")
            self.hotkeys = None
        if self.cfg.get("hotkeys_enabled", True):
            self._setup_hotkeys()
        self.panel.set_hotkeys(self.cfg["hotkeys"] if self.cfg.get("hotkeys_enabled", True) else {})

    # --- HoYoLAB : notes en temps réel, connexion quotidienne --------------------------
    def _bg_run(self, fn, ok, fail):
        from .uikit import Job
        job = Job(fn)
        self._bg.add(job)
        job.ok.connect(ok)
        job.fail.connect(lambda m: log.warning("HoYoLAB : %s", m))
        job.fail.connect(fail)
        job.finished.connect(lambda j=job: self._bg.discard(j))
        job.start()

    def _hoyo_client(self):
        from .hoyonotes import HoyoClient
        return HoyoClient(self.cfg.get("hoyolab_cookies") or {}, self.cfg.get("uid", ""))

    def refresh_notes(self, force=False):
        if self._notes_busy or not self.cfg.get("hoyolab_cookies") or not self.cfg.get("uid"):
            return
        if not force and not self.cfg.get("notes_enabled", True):
            return
        self._notes_busy = True

        def ok(n):
            self._notes_busy = False
            self.notes = n
            self.notes_updated.emit(n)
            self.render_notes()
            self._widgets_update()

        def fail(msg):
            self._notes_busy = False
            self.notes_updated.emit(msg)
            if force or self.notes is None:
                self.panel.set_notes(f"<span style='color:#e0a84a'>📒 {msg}</span>"
                                     if self.cfg.get("notes_enabled", True) else None)
        self._bg_run(lambda: self._hoyo_client().notes(), ok, fail)

    def render_notes(self):
        from .hoyonotes import duration, resin_now
        n = self.notes
        if not n or not self.cfg.get("notes_enabled", True) or not self.cfg.get("hoyolab_cookies"):
            self.panel.set_notes(None)
            return
        now = time.time()
        r = resin_now(n)
        mx = n["max_resin"]
        col = "#e07a5f" if r >= mx else ("#f5c542" if r >= mx * 0.8 else "#e8e6e3")
        full = "pleine !" if r >= mx else f"pleine dans {duration(n['resin_full_at'] - now)}"
        tasks = f"{n['tasks_done']}/{n['tasks_total']}" + (" ✓" if n["tasks_bonus"] else "")
        exp_done = n["exp_done"]
        if n["exp_total"] and now >= n["exp_next_at"] > n["fetched"]:   # une expédition a fini depuis
            exp_done = max(exp_done, 1)
        exp = f"{exp_done}/{n['exp_total']} prêtes" if exp_done else f"{n['exp_total']}/{n['exp_max']} en cours"
        coins = ""
        if n["max_coins"]:
            c_full = now >= n["coins_full_at"]
            coins = f" · 🏠 {'pleines !' if c_full else str(n['coins']) + '/' + str(n['max_coins'])}"
        tr = " · 🔧 transformateur prêt" if n.get("transformer_ready") else ""
        self.panel.set_notes(
            f"🌙 Résine <b style='color:{col}'>{r}</b>/{mx} · {full}<br>"
            f"<span style='color:#a7abb3'>📋 Commissions {tasks} · 🧭 Expéditions {exp}{coins}{tr}</span>")
        # Alerte résine
        thr = int(self.cfg.get("resin_alert", mx))
        if self.cfg.get("resin_alert_on", True) and r >= min(thr, mx):
            if not self._resin_alerted:
                self._resin_alerted = True
                self.notify("Résine", f"Ta résine est à {r}/{mx} : pense à la dépenser !")
        elif r < min(thr, mx):
            self._resin_alerted = False

    def notify(self, title: str, text: str):
        self.panel.flash(f"🔔 {text}")
        if sys.platform == "darwin":
            import subprocess
            t = text.replace('"', "'")
            subprocess.Popen(["osascript", "-e", f'display notification "{t}" with title "Genshin Overlay" '
                                                 f'subtitle "{title}" sound name "Glass"'])

    def check_update(self, manual=False):
        if self._update_busy or not self.updater.configured:
            return
        if not manual and not self.cfg.get("auto_update", True):
            return
        self._update_busy = True
        self._set_update_text("Recherche d'une mise à jour…")
        upd = self.updater

        def work():
            m = upd.check()
            if m is None:
                return None
            upd.install(m)
            return m

        def ok(m):
            self._update_busy = False
            if m is None:
                from .updater import current_version
                self._set_update_text(f"✓ À jour (version {current_version()}) — vérifié à {time.strftime('%H:%M')}.")
                return
            self.update_ready = m
            notes = m.get("notes", "")
            self._set_update_text(f"🔄 Version {m['version']} prête : redémarre l'app pour l'utiliser.\n{notes}")
            self.panel.show_update(m["version"])
            self.notify("Mise à jour", f"La version {m['version']} est prête : redémarre Genshin Overlay.")

        def fail(msg):
            self._update_busy = False
            log.warning("Mise à jour : %s", msg)
            self._set_update_text(f"⚠ {msg}")
        from .uikit import Job
        job = Job(work)
        self._bg.add(job)
        job.ok.connect(ok)
        job.fail.connect(fail)
        job.finished.connect(lambda j=job: self._bg.discard(j))
        job.start()

    def _set_update_text(self, t):
        self.update_text = t
        self.update_status.emit(t)

    def restart_for_update(self):
        save_config(self.cfg)
        from .updater import restart_app
        restart_app()

    def _app_state(self, state):
        from PyQt6.QtCore import Qt as _Qt
        if state == _Qt.ApplicationState.ApplicationActive and not self.panel.isVisible():
            self.show_panel()

    def show_panel(self):
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def background_ok(self) -> bool:
        """L'app reste joignable sans le panneau (mini-widget ou icône de la barre des menus) ?"""
        return bool((self.desk is not None and self.desk.active) or (self.menubar is not None and self.menubar.active))

    def _widgets_update(self):
        if self.menubar is not None:
            self.menubar.update()
        if self.desk is not None:
            self.desk.refresh()

    def set_desk_widget(self, on: bool):
        from .deskwidget import DeskWidget
        if self.cfg.get("desk_widget", True) != on:
            self.cfg["desk_widget"] = on
            save_config(self.cfg)
        if on and self.desk is None:
            self.desk = DeskWidget(self)
            self.desk.start()
        elif not on and self.desk is not None:
            self.desk.stop()
            self.desk = None
            if not self.background_ok() and not self.panel.isVisible():
                self.show_panel()

    def set_menubar(self, on: bool):
        from .menubar import MenuBarWidget
        if on and self.menubar is None:
            self.menubar = MenuBarWidget(self)
            self.menubar.start()
        elif not on and self.menubar is not None:
            self.menubar.stop()
            self.menubar = None
            if not self.panel.isVisible():
                self.panel.show()

    def check_events(self):
        """Calendrier des événements (pour la barre des menus) et notification « se termine demain »."""
        if not self.cfg.get("hoyolab_cookies") or not self.cfg.get("uid"):
            return

        def ok(cal):
            self.calendar = cal
            self._widgets_update()
            if not self.cfg.get("events_alert", True):
                return
            now = time.time()
            seen = set(self.cfg.get("notified_events") or [])
            soon = [e for e in cal["events"] if not e["finished"] and e["end"] and 0 < e["end"] - now < 36 * 3600
                    and f"{e['id']}:{e['end']}" not in seen]
            for e in soon[:3]:
                hours = int((e["end"] - now) // 3600)
                when = "demain" if hours >= 12 else f"dans {max(1, hours)} h"
                self.notify("Événement bientôt fini", f"« {e['name']} » se termine {when}.")
                seen.add(f"{e['id']}:{e['end']}")
            if soon:
                self.cfg["notified_events"] = sorted(seen)[-60:]
                save_config(self.cfg)
        self._bg_run(lambda: self._hoyo_client().calendar(), ok, lambda m: None)

    def check_errors(self):
        """Des erreurs attendent d'être envoyées : on le rappelle (au plus une fois par jour)."""
        from . import diag
        n = diag.pending_errors()
        if n < 1:
            return
        now = time.time()
        if now - float(self.cfg.get("errors_notified_at") or 0) < 24 * 3600:
            return
        self.cfg["errors_notified_at"] = now
        save_config(self.cfg)
        self.notify("Erreurs à envoyer", f"{n} erreur(s) gardée(s). Paramètres → Diagnostic → "
                                         "« Créer un rapport », puis envoie-le.")

    def do_checkin(self, manual=False):
        if not self.cfg.get("hoyolab_cookies") or not self.cfg.get("uid"):
            if manual:
                self.checkin_done.emit("⚠ Relie d'abord ton compte HoYoLAB (cookies + UID).")
            return
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))  # jour HoYoLAB (UTC+8)
        if not manual and (not self.cfg.get("auto_checkin", True) or self.cfg.get("last_checkin") == today):
            return

        def ok(res):
            state, reward = res
            self.cfg["last_checkin"] = today
            save_config(self.cfg)
            self.checkin_text = "🎁 Connexion HoYoLAB du jour : récupérée ✓"
            self._widgets_update()
            if state == "ok":
                msg = f"🎁 Connexion du jour récupérée : {reward}" if reward else "🎁 Connexion du jour récupérée."
                self.notify("Connexion quotidienne", msg[2:])
            else:
                msg = "✓ Récompense du jour déjà récupérée" + (f" ({reward})." if reward else ".")
            self.checkin_done.emit(msg)

        def fail(m):
            self.checkin_text = "🎁 Connexion HoYoLAB du jour : échec, nouvel essai dans 3 h"
            self._widgets_update()
            self.checkin_done.emit(f"⚠ {m}")
            if manual:
                self.panel.flash(f"⚠ Connexion du jour : {m}")
        self._bg_run(lambda: self._hoyo_client().checkin(), ok, fail)

    def _apply_region(self, region: dict):
        self.overlay.set_region(region["x"], region["y"], region["size"])
        if self.overlay_on:
            self.overlay.show()

    def _on_ready(self, n):
        self.ready = True
        if not self.cfg.get("minimap_region"):
            self.panel.set_status("Carte prête ✔<br>Clique sur <b>Calibrer la mini-map</b> pour commencer.")
        else:
            self.panel.set_status(f"Carte prête ({n:,} repères). Recherche du joueur…".replace(",", " "))

    def _on_worker_failed(self, msg):
        log.error("Localisation : %s", msg)
        from . import diag
        diag.snapshot("erreur_localisation")
        self.panel.set_status(f"<span style='color:#ff6b6b'>Erreur : {msg}</span>")

    def _diag_screen(self):
        """Écran du jeu pour les captures de diagnostic — seulement si le jeu était affiché récemment
        (mini-map vue il y a moins de 30 s), pour ne jamais capturer autre chose."""
        if time.time() - self._minimap_seen_at > 30:
            return None
        from .dialogue import frontmost_app
        if self.game_pid is None or frontmost_app()[0] != self.game_pid:
            return None   # une autre application est devant : on ne capture pas l'écran
        x, y = self._game_screen_point()
        img, _s, _r = self.capture.grab_screen_at(x, y)
        return img

    # --- boucle -----------------------------------------------------------------------
    def _tick(self):
        reg = self.cfg.get("minimap_region")
        if not (self.ready and reg and self.overlay_on):
            return
        try:
            frame, self.cap_scale = self.capture.grab(reg["x"], reg["y"], reg["size"])
        except CaptureError as e:
            self._capture_errors += 1
            if self._capture_errors in (1, 10) or self._capture_errors % 500 == 0:
                log.error("Capture de la mini-map impossible (%d fois) : %s", self._capture_errors, e)
            self.panel.set_status(f"<span style='color:#ff6b6b'>Capture impossible : {e}</span>")
            return
        if self.capture.probably_no_permission and not self._black_logged:
            self._black_logged = True
            log.warning("Image capturée noire : autorisation « Enregistrement de l'écran » manquante ?")
        if self.capture.probably_no_permission:
            if sys.platform == "win32":
                hint = ("Passe Genshin en mode <b>fenêtré</b> ou <b>plein écran fenêtré</b> "
                        "(Paramètres → Graphismes → Mode d'affichage) : le plein écran exclusif "
                        "empêche la capture.")
            else:
                hint = ("Autorise « Genshin Overlay » dans Réglages Système → Confidentialité et "
                        "sécurité → <b>Enregistrement de l'écran</b>, puis relance.")
            self.panel.set_status(f"<span style='color:#ff6b6b'>L'image capturée est noire.</span><br>{hint}")
            return
        self.mm_px = min(frame.shape[:2])
        if self.map_window is not None and self.map_window.isVisible():
            try:   # direction du perso et de la caméra, lue sur la mini-map
                self.heading = self.heading_filter.update(frame)
            except Exception as e:  # noqa: BLE001
                log.warning("orientation illisible : %s", e)
        self.worker.submit(frame, self.mask_pts, 9 * self.cap_scale, reg["size"])
        self._tick_n += 1
        if self.want_map and self.cfg.get("fullmap_enabled", True) and self._tick_n % 2 == 0:
            try:
                screen, self.screen_scale, rect = self.capture.grab_screen_at(
                    reg["x"] + reg["size"] / 2, reg["y"] + reg["size"] / 2)
            except CaptureError:
                return
            self.map_overlay.set_screen(rect)
            self.worker.submit_map(screen)

    def _on_located(self, r: LocateResult | None, ms: float):
        self.last_ms = ms
        loc = self.worker.locator
        if loc is not None and loc.minimap_visible:
            self._minimap_seen_at = time.time()
            if time.time() - self._game_pid_at > 2:   # quelle application affiche le jeu ?
                self._game_pid_at = time.time()
                from .dialogue import frontmost_app
                pid, name = frontmost_app()
                # même application devant 3 fois de suite (≈ 6 s) avec la mini-map visible = le jeu
                self._pid_votes = (self._pid_votes + 1) if pid == getattr(self, "_pid_cand", None) else 1
                self._pid_cand = pid
                if pid not in (-1, os.getpid()) and pid != self.game_pid and self._pid_votes >= 3:
                    self.game_pid = pid
                    log.info("Jeu détecté au premier plan : %s (pid %s)", name or "?", pid)
        # Joueur introuvable alors que la mini-map est visible : à noter dans le journal
        if r is None and loc is not None and loc.minimap_visible:
            if self._lost_since is None:
                self._lost_since = time.time()
            elif time.time() - self._lost_since > 20 and not getattr(self, "_lost_logged", False):
                self._lost_logged = True
                log.warning("Position introuvable depuis 20 s alors que la mini-map est visible")
                from . import diag
                diag.snapshot("position_introuvable")
        elif r is not None and self._lost_since is not None:
            if getattr(self, "_lost_logged", False):
                log.info("Position retrouvée après %.0f s", time.time() - self._lost_since)
            self._lost_since = None
            self._lost_logged = False
        if r is None and loc and not loc.minimap_visible:
            self.last = None
            self.want_map = True
            self.overlay.set_markers([], "idle")
            if not self.map_overlay.isVisible():
                self.panel.set_status("<span style='color:#888'>● Mini-map non visible</span><br>"
                                      "Menu, carte ou autre fenêtre ouverte : en attente…")
            return
        # La mini-map est revenue : on quitte le mode carte.
        if self.want_map or self.map_overlay.isVisible():
            self.want_map = False
            self.last_view = None
            self.map_overlay.hide()
        if r is None:
            if loc and loc.last is None:
                self.last = None
                self.overlay.set_markers([], "lost")
                self.panel.set_status(
                    f"<span style='color:#ff9f43'>● Recherche du joueur…</span><br>"
                    f"Vérifie que la mini-map est visible et bien calibrée.<br>{ms:.0f} ms / image")
            return
        self.last = r
        if self.map_window is not None and self.map_window.isVisible():
            self.map_window.set_player(np.asarray(r.pos) / self.data.scale, self.heading)
        if time.monotonic() - self._scales_saved_at > 20:
            self._save_scales()
        self._render()
        self.panel.set_status(
            f"<span style='color:#3ddc84'>● Position trouvée</span> ({r.mode})<br>"
            f"x={r.pos[0]:.0f}  y={r.pos[1]:.0f}  •  fiabilité {r.score:.0%}<br>"
            f"zoom {r.scale:.2f}  •  {ms:.0f} ms / image")

    # --- carte interactive ----------------------------------------------------------
    def open_map_window(self):
        if self.map_window is None:
            from .mapwindow import MapWindow
            self.map_window = MapWindow(self.data, self.cfg, lambda: save_config(self.cfg))
            self.map_window.ctrl = self
            self.map_window.progress_changed.connect(self._on_map_window_progress)
        if self.last is not None:
            self.map_window.set_player(np.asarray(self.last.pos) / self.data.scale)
        self.map_window.open()

    def _on_map_window_progress(self):
        """Un point a été coché/décoché dans la carte interactive : on met tout à jour."""
        self.panel.update_counts(self._counts())
        self._render()
        self._render_map()

    def _progress_updated(self):
        """La progression a changé depuis l'overlay : la carte interactive suit."""
        if self.map_window is not None:
            self.map_window.refresh_progress()

    def _on_map_located(self, v: MapView | None, size, ms: float):
        if not self.want_map:
            return  # résultat arrivé après la fermeture de la carte
        if v is None:
            self.map_misses += 1
            if self.map_misses >= 3 and self.map_overlay.isVisible():
                log.info("Carte plein écran : plus reconnue")
                self.last_view = None
                self.map_overlay.hide()
                self.panel.set_status("<span style='color:#888'>● Mini-map non visible</span><br>"
                                      "Menu, carte ou autre fenêtre ouverte : en attente…")
            return
        self.map_misses = 0
        self.last_view, self.view_size = v, size
        n = self._render_map()
        if self.overlay_on:
            if not self.map_overlay.isVisible():
                log.info("Carte plein écran reconnue : %d points, fiabilité %.0f %%", n, v.score * 100)
            self.map_overlay.show()
        self.panel.set_status(f"<span style='color:#3ddc84'>● Carte plein écran</span><br>"
                              f"{n} points affichés  •  fiabilité {v.score:.0%}  •  {ms:.0f} ms")

    def _render_map(self) -> int:
        v = self.last_view
        if v is None:
            return 0
        w, h = self.view_size
        colors = self._enabled()
        x0, y0, x1, y1 = v.capture_rect_in_map(w, h)
        xy = self.data.xy
        m = (xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)
        m &= np.isin(self.data.label_ids, list(colors))
        if not self.cfg.get("show_layered_points", True):
            m &= ~self.data.layered
        idx = np.nonzero(m)[0]
        done = self.data.progress.done
        idx = [i for i in idx if int(self.data.ids[i]) not in done]
        markers = []
        if idx:
            pts = v.map_to_capture(xy[idx])
            for i, (px, py) in zip(idx, pts):
                fx, fy = px / w, py / h
                # pas d'icônes sur l'interface du jeu (quêtes, barre du haut, nom de région…)
                if fy < 0.07 or (fx < 0.20 and fy < 0.30) or (fx > 0.78 and fy > 0.86):
                    continue
                lid = int(self.data.label_ids[i])
                markers.append(Marker(px / self.screen_scale, py / self.screen_scale, colors[lid], lid,
                                      bool(self.data.layered[i]), pid=int(self.data.ids[i])))
        n = len(markers)
        size = 26 if n <= 120 else 20 if n <= 400 else 14
        self.map_overlay.set_markers(markers, size)
        return n

    def _save_scales(self):
        """Mémorise les échelles rencontrées pour retrouver le joueur plus vite au prochain lancement."""
        self._scales_saved_at = time.monotonic()
        loc, k = self.worker.locator, self.worker.px_per_pt
        if not loc or not k or not loc.known_scales:
            return
        new = [round(s * k, 4) for s in loc.known_scales]
        if new != self.cfg.get("known_scales_pt"):
            self.cfg["known_scales_pt"] = new
            save_config(self.cfg)

    # --- dessin --------------------------------------------------------------------
    def _enabled(self) -> dict[int, str]:
        out = {}
        for g in self.cfg["groups"].values():
            if g.get("enabled"):
                for lid in g["labels"]:
                    out[lid] = g["color"]
        return out

    def _render(self):
        r = self.last
        if r is None or not self.mm_px:
            return
        colors = self._enabled()
        R = self.mm_px / 2 * 0.92            # rayon utile de la mini-map (px capturés)
        c = self.mm_px / 2
        idx = self.data.query_circle(r.pos[0], r.pos[1], R * r.scale * EDGE_RANGE, set(colors),
                                     self.cfg.get("show_layered_points", True))
        done = self.data.progress.done
        markers, mask = [], []
        if len(idx):
            pts = np.c_[self.data.xy[idx], np.ones(len(idx))] @ r.Minv.T   # -> px mini-map
            k = self.cap_scale
            for i, (x, y) in zip(idx, pts):
                if int(self.data.ids[i]) in done:
                    continue
                dx, dy = x - c, y - c
                d = float(np.hypot(dx, dy))
                lid = int(self.data.label_ids[i])
                if d <= R:
                    markers.append(Marker(x / k + MARGIN, y / k + MARGIN, colors[lid], lid,
                                          bool(self.data.layered[i])))
                    mask.append((x, y))
                else:  # hors champ : point au bord, dans la bonne direction
                    ex, ey = c + dx / d * R * 0.97, c + dy / d * R * 0.97
                    markers.append(Marker(ex / k + MARGIN, ey / k + MARGIN, colors[lid], lid, edge=True))
        self.mask_pts = mask
        self.overlay.set_markers(markers, "ok")

    def _counts(self) -> dict[str, tuple[int, int]]:
        done = self.data.progress.done
        ids_done = np.isin(self.data.ids, list(done)) if done else np.zeros(len(self.data.ids), bool)
        out = {}
        for name, g in self.cfg["groups"].items():
            m = np.isin(self.data.label_ids, g["labels"])
            out[name] = (int((m & ids_done).sum()), int(m.sum()))
        return out

    # --- actions -------------------------------------------------------------------
    def start_calibration(self):
        self.overlay.hide()
        self.calib = CalibrationWindow(self.cfg.get("minimap_region"))
        self.calib.done.connect(self._on_calibrated)
        self.calib.showFullScreen() if sys.platform != "darwin" else self.calib.show()

    def _on_calibrated(self, region):
        if region:
            self.cfg["minimap_region"] = region
            save_config(self.cfg)
            self.relocalize()
            self.panel.flash("Mini-map calibrée ✔")
        if self.cfg.get("minimap_region"):
            self._apply_region(self.cfg["minimap_region"])

    def mark_nearest(self):
        r = self.last
        if r is None:
            self.panel.flash("Position inconnue : impossible de marquer.")
            return
        rad = self.data.p0_to_map(self.cfg["mark_distance_p0"])
        idx = self.data.query_circle(r.pos[0], r.pos[1], rad, set(self._enabled()),
                                     self.cfg.get("show_layered_points", True))
        idx = [i for i in idx if int(self.data.ids[i]) not in self.data.progress.done]
        if not idx:
            self.panel.flash("Aucun point assez proche à marquer.")
            return
        d = [np.hypot(*(self.data.xy[i] - r.pos)) for i in idx]
        i = idx[int(np.argmin(d))]
        self.data.progress.mark(int(self.data.ids[i]))
        name = self.data.labels.get(int(self.data.label_ids[i]), {}).get("name", "Point")
        self.panel.flash(f"✔ {name} marqué comme fait.")
        self.panel.update_counts(self._counts())
        self._render()
        self._render_map()
        self._progress_updated()

    def undo_mark(self):
        n = self.data.progress.undo()
        self.panel.flash(f"Dernier marquage annulé ({n} point{'s' if n > 1 else ''})." if n else "Rien à annuler.")
        self.panel.update_counts(self._counts())
        self._render()
        self._render_map()
        self._progress_updated()

    def mark_view(self):
        """Marque comme faits tous les points affichés sur la carte plein écran
        (pratique pour une région déjà explorée à 100 %)."""
        if self.last_view is None or not self.map_overlay.isVisible():
            self.panel.flash("Ouvre d'abord la carte du jeu sur la zone à marquer.")
            return
        ids = [m.pid for m in self.map_overlay.markers if m.pid is not None]
        n = self.data.progress.mark_many(ids)
        self.panel.flash(f"✔ {n} points marqués comme faits (annulable avec « Annuler »).")
        self.panel.update_counts(self._counts())
        self._render()
        self._render_map()
        self._progress_updated()

    # --- aide de quête (IA) -----------------------------------------------------
    def _provider(self) -> str:
        return "claude" if self.cfg.get("quest_provider") == "claude" else "gemini"

    def _api_key(self) -> str:
        if self._provider() == "gemini":
            return (self.cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()
        return (self.cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()

    def _set_api_key(self, key: str):
        self.cfg["gemini_api_key" if self._provider() == "gemini" else "anthropic_api_key"] = key
        save_config(self.cfg)
        self.panel.flash("Clé API enregistrée ✔" if key else "Clé API supprimée.")

    def _quest_screen(self):
        reg = self.cfg.get("minimap_region")
        if reg:
            cx, cy = reg["x"] + reg["size"] / 2, reg["y"] + reg["size"] / 2
        else:
            g = self.app.primaryScreen().geometry()
            cx, cy = g.x() + g.width() / 2, g.y() + g.height() / 2
        return self.capture.grab_screen_at(cx, cy)

    def quest_help(self):
        """Si la carte d'aide est affichée : la ferme. Sinon : capture + question à l'IA."""
        if self.quest_card.isVisible():
            self.close_quest()
            return
        key = self._api_key()
        if not key:
            self.panel.flash("Ajoute d'abord ta clé API (bouton « Clé API… » du panneau).")
            self.panel.ask_api_key()
            return
        try:
            screen, _, rect = self._quest_screen()
        except CaptureError as e:
            self.panel.flash(f"Capture impossible : {e}")
            return
        self._quest_req += 1
        req = self._quest_req
        foot = f"Pour fermer : bouton « Fermer l'aide » ou {_hk(self.cfg, 'quest_help')}"
        self.quest_card.place(rect, self.cfg.get("quest_card", {}))
        self.quest_card.show_message("Aide de quête", "Lecture de la quête…", foot)
        self.panel.set_quest_open(True)
        model = (self.cfg.get("gemini_model", "") if self._provider() == "gemini"
                 else self.cfg.get("quest_model", "claude-sonnet-5-5"))
        w = QuestWorker(self._provider(), key, model, screen, self.panel.question.text())
        # Une demande fermée entre-temps est ignorée (req != self._quest_req).
        w.status.connect(lambda s: req == self._quest_req and
                         self.quest_card.show_message("Aide de quête", s, foot))
        w.answered.connect(lambda a: req == self._quest_req and self._on_quest_answer(a, foot))
        w.failed.connect(lambda m: req == self._quest_req and
                         self.quest_card.show_message("Aide de quête indisponible", m, foot))
        # On garde une référence tant que le fil tourne (sinon Qt plante en le détruisant).
        self._quest_workers.append(w)
        w.finished.connect(lambda w=w: self._quest_workers.remove(w) if w in self._quest_workers else None)
        w.start()

    def close_quest(self):
        """Ferme la carte d'aide (et ignore la réponse si elle n'est pas encore arrivée)."""
        self._quest_req += 1
        self.quest_card.hide()
        self.panel.set_quest_open(False)

    def _on_quest_answer(self, ans, foot):
        self.quest_card.show_answer(ans, foot)
        self.panel.question.clear()
        self.panel.flash(f"Aide reçue pour « {ans.quest or 'la quête'} ».")

    def reset_progress(self):
        self.data.progress.done.clear()
        self.data.progress.history.clear()
        self.data.progress.save()
        self.panel.update_counts(self._counts())
        self._render()
        self._render_map()
        self._progress_updated()

    def toggle_overlay(self):
        self.overlay_on = not self.overlay_on
        if self.overlay_on and self.cfg.get("minimap_region"):
            self.overlay.show()
        else:
            self.overlay.hide()
            self.map_overlay.hide()
        self.panel.flash("Overlay affiché." if self.overlay_on else "Overlay masqué.")

    def relocalize(self):
        self.worker.request_reset()
        self.last = None
        self.overlay.set_markers([], "lost")

    def _on_groups_changed(self):
        save_config(self.cfg)
        self.panel.update_counts(self._counts())
        self._render()
        self._render_map()

    def shutdown(self):
        if self.menubar is not None:
            self.menubar.stop()
        if self.desk is not None:
            self.desk.timer.stop()
        self._save_scales()
        save_config(self.cfg)
        if self.hotkeys:
            self.hotkeys.stop()
        self.worker.stop()
        self.worker.wait(2000)


def _hk(cfg, name) -> str:
    from .panel import _pretty
    return _pretty(cfg["hotkeys"].get(name, ""))


def main() -> int:
    macos.set_app_identity("Genshin Overlay")
    app = QApplication(sys.argv)
    app.setApplicationName("Genshin Overlay")
    app.setQuitOnLastWindowClosed(False)
    cfg = load_config()
    if cfg.get("map_id", 2) != 2 and not (map_dir(cfg["map_id"]) / "meta.json").exists():
        cfg["map_id"] = 2          # dimension absente : retour à Teyvat
        save_config(cfg)
    folder = map_dir(cfg["map_id"])
    missing = [f for f in ("meta.json", "points.json", "labels.json", "map_gray.png") if not (folder / f).exists()]
    if missing:
        QMessageBox.critical(None, "Données manquantes",
                             "Les données de la carte ne sont pas encore téléchargées.\n\n"
                             "Lance d'abord : python fetch_data.py\n"
                             f"(dossier attendu : {folder})")
        return 1
    macos.set_accessory_app()
    try:
        ctrl = Controller(app)
    except ImportError as e:
        QMessageBox.critical(None, "Dépendance manquante", f"{e}\n\nLance : pip install -r requirements.txt")
        return 1
    ctrl.panel.destroyed.connect(app.quit)

    def started_ok():   # démarrage réussi : la mise à jour n'est pas « défectueuse »
        sup, ver = os.environ.get("GIO_SUP"), ""
        try:
            from .updater import current_version
            ver = current_version()
            if sup:
                (__import__("pathlib").Path(sup) / "code" / f"starting-{ver}").unlink(missing_ok=True)
        except OSError:
            pass
        log.info("Démarrage réussi (version %s)", ver)
    QTimer.singleShot(15000, started_ok)
    # Pas d'icône dans le Dock au démarrage : elle n'apparaît que quand la grande fenêtre
    # (Carte / Builds / …) est ouverte, sinon l'overlay passe derrière le jeu en plein écran.

    def on_close(e):  # fermer le panneau quitte l'application… sauf si le widget de la barre des menus est là
        if ctrl.background_ok():
            e.ignore()
            ctrl.panel.hide()
            ctrl._widgets_update()
            if not ctrl.cfg.get("bg_hint_shown"):
                ctrl.cfg["bg_hint_shown"] = True
                save_config(ctrl.cfg)
                ctrl.notify("Toujours là", "Genshin Overlay continue en arrière-plan (mini-widget). "
                                           "Clic sur le widget : panneau. Clic droit : Quitter.")
            return
        e.accept()
        app.quit()

    ctrl.panel.closeEvent = on_close
    return app.exec()
