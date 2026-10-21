"""Carte interactive (fenêtre séparée), comme la carte HoYoLAB :
- fond de carte en tuiles HoYoLAB (téléchargées à la demande, gardées en cache sur le disque),
- catalogue à gauche pour choisir ce qu'on affiche,
- clic sur un point = coché « récupéré » (ou décoché),
- même progression que l'overlay : tout reste synchronisé.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QGraphicsItem, QGraphicsPixmapItem, QGraphicsScene, QHeaderView,
                             QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter,
                             QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import macos

TILE_URL = "https://act-webstatic.hoyoverse.com/map_manage/map/{map_id}/{version}/{x}_{y}_{res}.webp"
LODS = [("N3", 8), ("N2", 4), ("N1", 2), ("P0", 1)]   # une tuile de 256 px couvre 256 × facteur px « P0 »
MAX_TILES = 700
MAX_REQUESTS = 12
MAX_ZOOM = 2.5             # au-delà, les tuiles HoYoLAB (P0 = résolution maximale) sont trop agrandies
GROUP_ORDER = ["Coffre", "Coffres d'énigme", "Objets spéciaux", "Repères importants", "Repères",
               "Inventaire / Matériaux", "Produits régionaux", "Minerais", "Bois", "Connaissances",
               "Ennemis", "Animaux", "Pêche"]
HEADERS = {b"User-Agent": b"Mozilla/5.0 GenshinOverlay", b"Referer": b"https://act.hoyolab.com/"}

STYLE = """
QWidget#side { background: #16181d; }
QWidget#side QLabel, QWidget#side QCheckBox { color: #e8e6e3; }
QLabel#h { font-size: 16px; font-weight: 600; color: #f5c542; }
QLabel#stats { color: #8b8f98; font-size: 11px; }
QLineEdit { background: #1f2229; color: #e8e6e3; border: 1px solid #383d48; border-radius: 7px; padding: 6px 8px; }
QTreeWidget { background: #1b1e24; color: #e8e6e3; border: none; font-size: 13px; }
QTreeWidget::item { padding: 3px 0; }
QTreeWidget::item:selected { background: #2a2e37; }
QPushButton { background: #2a2e37; color: #e8e6e3; border: 1px solid #383d48; border-radius: 7px; padding: 6px 10px; }
QPushButton:hover { background: #333845; }
QPushButton#follow:checked { background: #f5c542; color: #16181d; border-color: #f5c542; font-weight: 700; }
QGraphicsView { background: #0d1520; border: none; }
QWidget#nav { background: #0f1115; border-bottom: 1px solid #262a33; }
QLabel#brand { color: #f5c542; font-size: 15px; font-weight: 700; padding-right: 14px; }
QPushButton#tab { background: transparent; border: none; border-bottom: 2px solid transparent;
                  border-radius: 0; color: #a7abb3; font-size: 14px; font-weight: 600; padding: 10px 16px; }
QPushButton#tab:hover { color: #e8e6e3; background: #181b21; }
QPushButton#tab:checked { color: #f5c542; border-bottom: 2px solid #f5c542; }
"""


class Net:
    """Téléchargements (tuiles, icônes) avec file d'attente et cache disque."""

    def __init__(self):
        self.nam = QNetworkAccessManager()
        self.queue: list[tuple[str, Path, object]] = []
        self.active = 0
        self.pending: set[str] = set()

    def get(self, url: str, dest: Path, done, front=False):
        if url in self.pending:
            return
        self.pending.add(url)
        item = (url, dest, done)
        self.queue.insert(0, item) if front else self.queue.append(item)
        self._pump()

    def clear_queue(self):
        for url, _, _ in self.queue:
            self.pending.discard(url)
        self.queue.clear()

    def _pump(self):
        while self.active < MAX_REQUESTS and self.queue:
            url, dest, done = self.queue.pop(0)
            req = QNetworkRequest(QUrl(url))
            for k, v in HEADERS.items():
                req.setRawHeader(k, v)
            reply = self.nam.get(req)
            self.active += 1
            reply.finished.connect(lambda r=reply, u=url, d=dest, cb=done: self._finished(r, u, d, cb))

    def _finished(self, reply: QNetworkReply, url, dest: Path, done):
        self.active -= 1
        self.pending.discard(url)
        ok = reply.error() == QNetworkReply.NetworkError.NoError
        if not ok and reply.error() != QNetworkReply.NetworkError.ContentNotFoundError:
            self.errors = getattr(self, "errors", 0) + 1
            if self.errors in (1, 20) or self.errors % 200 == 0:
                import logging
                logging.getLogger("gio.carte").warning("Téléchargement impossible (%d fois) : %s — %s",
                                                       self.errors, reply.errorString(), url)
        data = bytes(reply.readAll()) if ok else b""
        reply.deleteLater()
        if ok and data:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            except OSError:
                ok = False
        done(ok and bool(data))
        self._pump()


class MarkerItem(QGraphicsItem):
    SIZE = 24

    def __init__(self, win: "MapWindow", idx: int):
        super().__init__()
        self.win, self.idx = win, idx
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(10)
        self.hover = False

    def boundingRect(self):
        s = self.SIZE / 2 + 3
        return QRectF(-s, -s, 2 * s, 2 * s)

    def paint(self, p: QPainter, *_):
        d = self.win.data
        lid = int(d.label_ids[self.idx])
        done = int(d.ids[self.idx]) in d.progress.done
        r = self.SIZE / 2
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(0.45 if done else 1.0)
        ring = QColor("#3ddc84") if done else QColor("#f5c542" if self.hover else "#e8e6e3")
        p.setPen(QPen(ring, 2.5 if self.hover else 1.5))
        p.setBrush(QColor(12, 16, 26, 200))
        p.drawEllipse(QRectF(-r, -r, 2 * r, 2 * r))
        icon = self.win.icon(lid)
        if icon is not None:
            p.drawPixmap(QRectF(-r + 3, -r + 3, 2 * r - 6, 2 * r - 6), icon, QRectF(icon.rect()))
        if done:
            p.setOpacity(1.0)
            path = QPainterPath()
            path.moveTo(-6, 0)
            path.lineTo(-1.5, 5)
            path.lineTo(7, -5)
            p.setPen(QPen(QColor("#3ddc84"), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

    def hoverEnterEvent(self, e):
        self.hover = True
        d = self.win.data
        lab = d.labels.get(int(d.label_ids[self.idx]), {})
        state = "récupéré ✔" if int(d.ids[self.idx]) in d.progress.done else "clic pour cocher"
        self.setToolTip(f"{lab.get('name', 'Point')} — {state}")
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.win.toggle_point(self.idx)
            e.accept()
        else:
            super().mousePressEvent(e)


class PlayerItem(QGraphicsItem):
    """Position du joueur, avec la direction du personnage (flèche) et de la caméra (cône),
    lues sur la mini-map. Angles en degrés, sens horaire depuis le nord."""
    CONE_R = 90

    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(50)
        self.arrow: float | None = None
        self.cone: float | None = None

    def set_heading(self, arrow, cone):
        arrow = None if arrow is None else float(arrow)
        cone = None if cone is None else float(cone)
        if arrow != self.arrow or cone != self.cone:
            self.arrow, self.cone = arrow, cone
            self.update()

    def boundingRect(self):
        r = self.CONE_R + 4
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, p: QPainter, *_):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.cone is not None:   # cône de vision (caméra), dégradé qui s'efface
            from PyQt6.QtGui import QRadialGradient
            g = QRadialGradient(0, 0, self.CONE_R)
            g.setColorAt(0.0, QColor(255, 255, 255, 210))
            g.setColorAt(0.35, QColor(120, 230, 255, 150))
            g.setColorAt(1.0, QColor(90, 220, 255, 0))
            path = QPainterPath()
            path.moveTo(0, 0)
            # Qt : 0° = est, sens antihoraire ; notre angle : 0° = nord, sens horaire
            start = 90 - self.cone - 40
            path.arcTo(QRectF(-self.CONE_R, -self.CONE_R, 2 * self.CONE_R, 2 * self.CONE_R), start, 80)
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawPath(path)
        p.setPen(QPen(QColor(90, 220, 255, 110), 6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(-12, -12, 24, 24))
        if self.arrow is not None:  # flèche du personnage
            p.save()
            p.rotate(self.arrow)
            tri = QPainterPath()
            tri.moveTo(0, -18)
            tri.lineTo(11, 11)
            tri.lineTo(0, 5)
            tri.lineTo(-11, 11)
            tri.closeSubpath()
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(QColor("#5adcff"))
            p.drawPath(tri)
            p.restore()
        else:
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(QColor("#5adcff"))
            p.drawEllipse(QRectF(-6, -6, 12, 12))


def _sharp_pixmap(path: Path) -> QPixmap:
    """Tuile la plus détaillée, légèrement renforcée (netteté) pour les zooms rapprochés."""
    try:
        import cv2
        a = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if a is None:
            return QPixmap(str(path))
        rgb = a[:, :, :3]
        blur = cv2.GaussianBlur(rgb, (0, 0), 1.0)
        rgb = cv2.addWeighted(rgb, 1.45, blur, -0.45, 0)
        if a.ndim == 3 and a.shape[2] == 4:
            a = np.dstack([cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB), a[:, :, 3]])
            img = QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format.Format_RGBA8888)
        else:
            a = np.ascontiguousarray(cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB))
            img = QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img.copy())
    except Exception:  # noqa: BLE001
        return QPixmap(str(path))


class MapView(QGraphicsView):
    viewport_changed = pyqtSignal()
    user_panned = pyqtSignal()   # l'utilisateur a déplacé la carte à la main (désactive le suivi)

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor("#0d1520")))
        self.horizontalScrollBar().valueChanged.connect(self.viewport_changed)
        self.verticalScrollBar().valueChanged.connect(self.viewport_changed)

    def scale_level(self) -> float:
        return self.transform().m11()

    def wheelEvent(self, e):
        delta = e.angleDelta().y() or e.pixelDelta().y()
        if not delta:
            return
        f = 1.0015 ** delta
        s = self.scale_level()
        lo = self.min_scale()
        f = max(lo / s, min(MAX_ZOOM / s, f))
        self.scale(f, f)
        self.viewport_changed.emit()

    def mousePressEvent(self, e):
        self._press = e.position()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        p = getattr(self, "_press", None)
        if p is not None and e.buttons() & Qt.MouseButton.LeftButton:
            if (e.position() - p).manhattanLength() > 8:
                self._press = None
                self.user_panned.emit()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._press = None
        super().mouseReleaseEvent(e)

    def min_scale(self) -> float:
        r = self.sceneRect()
        vp = self.viewport().size()
        return 0.8 * min(vp.width() / max(1, r.width()), vp.height() / max(1, r.height()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.viewport_changed.emit()

    def visible_scene_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()


class MapWindow(QWidget):
    progress_changed = pyqtSignal()   # l'utilisateur a coché / décoché un point

    def __init__(self, data, cfg: dict, save_cfg):
        super().__init__(None, Qt.WindowType.Window)
        self.data, self.cfg, self.save_cfg = data, cfg, save_cfg
        self.setWindowTitle("Carte interactive — Genshin Overlay")
        self.setStyleSheet(STYLE)
        self.resize(1400, 860)
        meta = data.meta
        self.W0, self.H0 = meta["total_size"]
        self.tiles_dir = data.folder / "tiles"
        self.net = Net()
        self.tiles: OrderedDict[tuple, QGraphicsPixmapItem] = OrderedDict()
        self.missing: set[tuple] = set()
        self._icons: dict[int, QPixmap | None] = {}
        self._icon_wait: set[int] = set()
        self.markers: dict[int, MarkerItem] = {}
        self.selected: set[int] = set(cfg.get("map_window_labels") or [17, 44, 45, 46, 269, 64])

        # --- scène ----------------------------------------------------------------------
        self.scene = QGraphicsScene(self)
        m = 2000
        self.scene.setSceneRect(QRectF(-m, -m, self.W0 + 2 * m, self.H0 + 2 * m))
        prev = data.folder / "preview.jpg"
        if prev.exists():  # fond immédiat (basse résolution) en attendant les tuiles
            base = QGraphicsPixmapItem(QPixmap(str(prev)))
            base.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            base.setScale(1.0 / (meta["scale"] * meta.get("preview_scale", 1.0)))
            base.setZValue(-10)
            self.scene.addItem(base)
        self.player = PlayerItem()
        self.player.setVisible(False)
        self.scene.addItem(self.player)
        self.view = MapView(self.scene)
        self.view.viewport_changed.connect(self._schedule_tiles)
        self.view.user_panned.connect(lambda: self.following and self.set_follow(False, reason="moved"))
        self.following = False
        self._follow_timer = QTimer(self, interval=33, timeout=self._follow_step)
        self._tile_timer = QTimer(self, singleShot=True, interval=120, timeout=self._update_tiles)

        # --- catalogue ------------------------------------------------------------------
        side = QWidget()
        side.setObjectName("side")
        side.setMinimumWidth(330)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 12, 12, 12)
        h = QLabel("Catalogue")
        h.setObjectName("h")
        sl.addWidget(h)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher (coffre, fée, oculus, cecilia…)")
        self.search.textChanged.connect(self._filter)
        sl.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setIconSize(QSize(22, 22))
        self.tree.setUniformRowHeights(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        sl.addWidget(self.tree, 1)
        self.hide_done = QCheckBox("Masquer ce que j'ai déjà récupéré")
        self.hide_done.setChecked(bool(cfg.get("map_window_hide_done", False)))
        self.hide_done.toggled.connect(self._hide_done_toggled)
        sl.addWidget(self.hide_done)
        row = QHBoxLayout()
        b_me = QPushButton("📍 Me suivre")
        b_me.setObjectName("follow")
        b_me.setCheckable(True)
        b_me.setToolTip("Activé : la carte suit ton personnage en direct (comme un GPS).\n"
                        "Déplacer la carte à la main désactive le suivi.")
        b_me.toggled.connect(self.set_follow)
        self.b_follow = b_me
        b_all = QPushButton("🗺 Toute la carte")
        b_all.clicked.connect(self.fit_all)
        row.addWidget(b_me)
        row.addWidget(b_all)
        sl.addLayout(row)
        self._saved_selection = None
        self.b_restore = QPushButton("↩ Rétablir ma sélection")
        self.b_restore.setToolTip("Réaffiche ce que tu avais coché avant d'ouvrir un matériau depuis Builds")
        self.b_restore.clicked.connect(self.restore_selection)
        self.b_restore.hide()
        sl.addWidget(self.b_restore)
        b_none = QPushButton("Tout décocher dans le catalogue")
        b_none.clicked.connect(self._clear_selection)
        sl.addWidget(b_none)
        self.stats = QLabel()
        self.stats.setObjectName("stats")
        self.stats.setWordWrap(True)
        sl.addWidget(self.stats)

        split = QSplitter()
        split.addWidget(side)
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 1060])

        # --- barre de navigation (Carte | Builds) ----------------------------------------
        nav = QWidget()
        nav.setObjectName("nav")
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(16, 0, 16, 0)
        nl.setSpacing(2)
        brand = QLabel("Genshin Overlay")
        brand.setObjectName("brand")
        nl.addWidget(brand)
        self.tabs = QButtonGroup(self)
        for i, label in enumerate(("🗺  Carte", "⚔  Builds", "📰  News", "📊  Compte", "⚙  Paramètres")):
            b = QPushButton(label)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.tabs.addButton(b, i)
            nl.addWidget(b)
        nl.addStretch(1)
        self.tabs.button(0).setChecked(True)
        self.tabs.idClicked.connect(self.show_tab)
        self.stack = QStackedWidget()
        self.stack.addWidget(split)
        self.build_page = self.news_page = self.settings_page = self.account_page = None
        self.ctrl = None   # contrôleur de l'overlay (raccourcis, notes HoYoLAB), donné par l'application
        self._gamedata = self._roster = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(nav)
        lay.addWidget(self.stack, 1)

        self._build_tree()
        self._rebuild_markers()
        self._fitted = False

    # ------------------------------------------------------------------ fenêtre
    def _shared(self):
        """Données partagées par Builds et News (créées à la première utilisation)."""
        if self._gamedata is None:
            from .gamedata import GameData
            from .roster import Roster
            root = self.data.folder.parent.parent   # dossier « data » de l'application
            self._gamedata = GameData(root / "gamedata")
            self._roster = Roster(root / "roster.json")
        return self._gamedata, self._roster

    def show_tab(self, i: int):
        pages = {1: "build_page", 2: "news_page", 3: "account_page", 4: "settings_page"}
        if i in pages and getattr(self, pages[i]) is None:   # créée à la première visite seulement
            gd, roster = self._shared()
            root = self.data.folder.parent.parent
            if i == 1:
                from .buildpage import BuildPage
                from .builds import BuildStore
                page = BuildPage(BuildStore(root / "builds"), self.cfg, self.save_cfg, gd, roster,
                                 show_on_map=self.show_label, has_map_label=lambda n: self.find_label(n) is not None)
                if page.roster_view is not None:
                    page.roster_view.import_requested.connect(self._import_from_roster)
            elif i == 2:
                from .news import NewsStore
                from .newspage import NewsPage
                page = NewsPage(NewsStore(root / "news"), gd, roster)
            elif i == 3:
                from .accountpage import AccountPage
                page = AccountPage(self.cfg, root / "account", self.overlay_chest_counts, lambda: self.show_tab(4))
            else:
                from .settingspage import SettingsPage
                page = SettingsPage(self.cfg, self.save_cfg, self._shared, self.ctrl)
                page.roster_imported.connect(self._roster_imported)
            setattr(self, pages[i], page)
            page.setProperty("tab", i)
            self.stack.addWidget(page)
        self.tabs.button(i).setChecked(True)
        page = self.stack.widget(0) if i == 0 else getattr(self, pages[i])
        self.stack.setCurrentWidget(page)
        self.setWindowTitle(("Carte interactive", "Builds", "News", "Compte", "Paramètres")[i] + " — Genshin Overlay")
        if i == 0:
            self._schedule_tiles()

    def _import_from_roster(self):
        self.show_tab(4)
        self.settings_page.import_showcase()

    def overlay_chest_counts(self) -> dict:
        """Nombre de points cochés « récupéré » dans l'overlay, par nom de catégorie (ex. « Coffre luxueux »)."""
        d = self.data
        done = np.isin(d.ids, np.fromiter(d.progress.done, dtype=d.ids.dtype)) if d.progress.done else \
            np.zeros(len(d.ids), bool)
        out = {}
        for lid, lab in d.labels.items():
            out[lab.get("name", "")] = out.get(lab.get("name", ""), 0) + int((done & (d.label_ids == lid)).sum())
        return out

    def _roster_imported(self):
        bp = self.build_page
        if bp is not None and bp.roster_view is not None:
            bp.roster_view.reload_roster()

    # ------------------------------------------------------------------ lien Builds -> carte
    def find_label(self, name: str) -> int | None:
        from .rosterpage import norm
        n = norm(name)
        if not n:
            return None
        for lid, lab in self.data.labels.items():
            if norm(lab.get("name", "")) == n and bool((self.data.label_ids == lid).any()):
                return int(lid)
        return None

    def _set_selection(self, sel: set[int]):
        self.tree.blockSignals(True)
        for l, it in self._tree_items.items():
            it.setCheckState(0, Qt.CheckState.Checked if l in sel else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self.selected = set(sel)
        self._selection_applied()

    def restore_selection(self):
        saved, self._saved_selection = self._saved_selection, None
        if saved is not None:
            self._set_selection(saved)
        self.b_restore.hide()

    def show_label(self, name: str) -> bool:
        """Affiche tous les emplacements d'un matériau (ex. « Cécilia ») et cadre la carte dessus."""
        lid = self.find_label(name)
        if lid is None:
            return False
        self.search.setText("")
        it = self._tree_items.get(lid)
        if it is not None:
            self.tree.blockSignals(True)
            it.setCheckState(0, Qt.CheckState.Checked)
            self.tree.blockSignals(False)
            self.tree.scrollToItem(it)
        # on n'affiche que ce matériau ; la sélection précédente peut être rétablie
        if self._saved_selection is None:
            self._saved_selection = set(self.selected)
        self._set_selection({lid})
        self.b_restore.show()
        self.show_tab(0)
        if self.following:
            self.set_follow(False)
        pts = self.data.p0[self.data.label_ids == lid]
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        m = 700
        self.view.resetTransform()
        self.view.fitInView(QRectF(x0 - m, y0 - m, (x1 - x0) + 2 * m, (y1 - y0) + 2 * m),
                            Qt.AspectRatioMode.KeepAspectRatio)
        self._fitted = True
        self._schedule_tiles()
        self.stats.setText(f"📍 {self.data.labels[lid]['name']} : {len(pts)} emplacement(s) affiché(s).")
        return True

    def _dock(self, on: bool):
        """Icône dans le Dock seulement pendant que cette fenêtre est ouverte : avec une icône,
        macOS refuse d'afficher l'overlay par-dessus un jeu en plein écran (autre « Space »)."""
        import os
        return   # plus d'icône dans le Dock (l'overlay passait derrière le jeu en plein écran)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._dock(False)

    def open(self):
        self._dock(True)
        self.show()
        if not self._fitted:
            self._fitted = True
            QTimer.singleShot(0, self.fit_all)
            if self.cfg.get("map_follow"):
                QTimer.singleShot(50, lambda: self.set_follow(True))
        self.raise_()
        self.activateWindow()
        macos.activate_app()

    def fit_all(self):
        if self.following:
            self.set_follow(False)
        self.view.resetTransform()
        r = QRectF(0, 0, self.W0, self.H0)
        self.view.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)
        self._schedule_tiles()

    # ------------------------------------------------------------------ tuiles
    def _schedule_tiles(self):
        if not self._tile_timer.isActive():   # au plus une mise à jour toutes les 120 ms, même en mouvement
            self._tile_timer.start()

    def _lod(self) -> tuple[str, int] | None:
        # Pixels réels de l'écran (×2 sur un écran Retina) par pixel « P0 » de la carte :
        # on prend la résolution de tuiles qui donne au moins 1 pixel de carte par pixel d'écran.
        s = self.view.scale_level() * max(1.0, self.view.devicePixelRatioF())
        if s > 0.55:
            return LODS[3]
        if s > 0.275:
            return LODS[2]
        if s > 0.1375:
            return LODS[1]
        if s > 0.06:
            return LODS[0]
        return None  # vue d'ensemble : l'aperçu suffit

    def _update_tiles(self):
        lod = self._lod()
        if lod is None:
            return
        res, f = lod
        span = 256 * f
        r = self.view.visible_scene_rect()
        x0, x1 = max(0, int(r.left() // span)), min(self.W0 // span - 1, int(r.right() // span))
        y0, y1 = max(0, int(r.top() // span)), min(self.H0 // span - 1, int(r.bottom() // span))
        cx, cy = r.center().x() / span, r.center().y() / span
        wanted = sorted(((x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)),
                        key=lambda t: (t[0] + 0.5 - cx) ** 2 + (t[1] + 0.5 - cy) ** 2)
        self.net.clear_queue()  # on ne télécharge que ce qui est visible maintenant
        for x, y in wanted:
            key = (res, x, y)
            if key in self.tiles:
                self.tiles.move_to_end(key)
                continue
            if key in self.missing:
                continue
            path = self.tiles_dir / res / f"{x}_{y}.webp"
            if path.exists():
                self._add_tile(key, path)
            else:
                url = TILE_URL.format(map_id=self.data.meta["map_id"], version=self.data.meta["map_version"],
                                      x=x, y=y, res=res)
                self.net.get(url, path, lambda ok, k=key, p=path: self._tile_done(k, p, ok))
        while len(self.tiles) > MAX_TILES:
            _, item = self.tiles.popitem(last=False)
            self.scene.removeItem(item)

    def _tile_done(self, key, path, ok):
        if ok:
            self._add_tile(key, path)
        else:
            self.missing.add(key)

    def _add_tile(self, key, path: Path):
        if key in self.tiles:
            return
        res, x, y = key
        pm = _sharp_pixmap(path) if res == "P0" else QPixmap(str(path))
        if pm.isNull():
            self.missing.add(key)
            return
        f = dict(LODS)[res]
        item = QGraphicsPixmapItem(pm)
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setScale(f)
        item.setPos(x * 256 * f, y * 256 * f)
        item.setZValue({"N3": -6, "N2": -5, "N1": -4, "P0": -3}[res])
        self.scene.addItem(item)
        self.tiles[key] = item

    # ------------------------------------------------------------------ icônes
    def icon(self, lid: int) -> QPixmap | None:
        if lid in self._icons:
            return self._icons[lid]
        path = self.data.icon_path(lid)
        if path.exists():
            pm = QPixmap(str(path))
            self._icons[lid] = None if pm.isNull() else pm.scaled(
                40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            return self._icons[lid]
        url = self.data.labels.get(lid, {}).get("icon")
        if url and lid not in self._icon_wait:
            self._icon_wait.add(lid)
            self.net.get(url, path, lambda ok, l=lid: self._icon_done(l, ok), front=True)
        return None

    def _icon_done(self, lid, ok):
        self._icon_wait.discard(lid)
        self._icons.pop(lid, None)
        if ok:
            pm = self.icon(lid)
            if pm is not None:
                it = self._tree_items.get(lid)
                if it is not None:
                    it.setIcon(0, QIcon(pm))
                self.scene.update()

    # ------------------------------------------------------------------ catalogue
    def _counts(self):
        d = self.data
        done = np.isin(d.ids, list(d.progress.done)) if d.progress.done else np.zeros(len(d.ids), bool)
        lids, total = np.unique(d.label_ids, return_counts=True)
        dlids, dcnt = np.unique(d.label_ids[done], return_counts=True)
        dmap = dict(zip(dlids.tolist(), dcnt.tolist()))
        return {int(l): (dmap.get(int(l), 0), int(t)) for l, t in zip(lids, total)}

    def _build_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        self._tree_items: dict[int, QTreeWidgetItem] = {}
        counts = self._counts()
        groups: dict[str, list[int]] = {}
        for lid, lab in self.data.labels.items():
            if counts.get(lid, (0, 0))[1]:
                groups.setdefault(lab.get("group", "Autres"), []).append(lid)
        order = sorted(groups, key=lambda g: (GROUP_ORDER.index(g) if g in GROUP_ORDER else 99, g))
        for g in order:
            top = QTreeWidgetItem([g, ""])
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            for lid in sorted(groups[g], key=lambda l: self.data.labels[l]["name"]):
                it = QTreeWidgetItem([self.data.labels[lid]["name"], ""])
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setData(0, Qt.ItemDataRole.UserRole, lid)
                it.setCheckState(0, Qt.CheckState.Checked if lid in self.selected else Qt.CheckState.Unchecked)
                it.setForeground(1, QBrush(QColor("#8b8f98")))
                top.addChild(it)
                self._tree_items[lid] = it
            top.setExpanded(any(l in self.selected for l in groups[g]) or g in GROUP_ORDER[:2])
        self.tree.blockSignals(False)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemExpanded.connect(self._load_icons)
        self._update_counts(counts)
        self._load_icons()

    def _load_icons(self, *_):
        """Icônes des catégories visibles du catalogue (téléchargées au besoin)."""
        for lid, it in self._tree_items.items():
            if it.parent() and it.parent().isExpanded() and not it.isHidden():
                pm = self.icon(lid)
                if pm is not None and it.icon(0).isNull():
                    it.setIcon(0, QIcon(pm))

    def _update_counts(self, counts=None):
        counts = counts or self._counts()
        self.tree.blockSignals(True)
        for lid, it in self._tree_items.items():
            d, t = counts.get(lid, (0, 0))
            it.setText(1, f"{d}/{t}")
        self.tree.blockSignals(False)
        sel_d = sum(counts.get(l, (0, 0))[0] for l in self.selected)
        sel_t = sum(counts.get(l, (0, 0))[1] for l in self.selected)
        self.stats.setText(f"Affichés : {sel_t - sel_d} restants sur {sel_t} "
                           f"({sel_d} récupérés). Clic sur un point pour le cocher ou le décocher.")

    def _item_changed(self, item, col):
        lid = item.data(0, Qt.ItemDataRole.UserRole)
        if lid is None:
            return  # case d'un groupe : Qt met à jour les enfants, qui déclenchent ce signal
        if self._saved_selection is not None:   # l'utilisateur modifie lui-même : on garde sa nouvelle sélection
            self._saved_selection = None
            self.b_restore.hide()
        if item.checkState(0) == Qt.CheckState.Checked:
            self.selected.add(int(lid))
        else:
            self.selected.discard(int(lid))
        self._sel_timer = getattr(self, "_sel_timer", None) or QTimer(self, singleShot=True, interval=60,
                                                                       timeout=self._selection_applied)
        self._sel_timer.start()

    def _selection_applied(self):
        if getattr(self, "_saved_selection", None) is None:   # affichage temporaire d'un matériau : pas sauvegardé
            self.cfg["map_window_labels"] = sorted(self.selected)
            self.save_cfg()
        self._rebuild_markers()
        self._update_counts()

    def _clear_selection(self):
        self.tree.blockSignals(True)
        for it in self._tree_items.values():
            it.setCheckState(0, Qt.CheckState.Unchecked)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self.selected.clear()
        self._selection_applied()

    def _filter(self, text):
        t = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            any_vis = False
            for j in range(top.childCount()):
                ch = top.child(j)
                vis = not t or t in ch.text(0).lower() or t in top.text(0).lower()
                ch.setHidden(not vis)
                any_vis |= vis
            top.setHidden(not any_vis)
            if t and any_vis:
                top.setExpanded(True)
        self._load_icons()

    def _hide_done_toggled(self, on):
        self.cfg["map_window_hide_done"] = on
        self.save_cfg()
        self._rebuild_markers()

    # ------------------------------------------------------------------ points
    def _rebuild_markers(self):
        for it in self.markers.values():
            self.scene.removeItem(it)
        self.markers.clear()
        d = self.data
        if not self.selected:
            return
        idx = np.nonzero(np.isin(d.label_ids, list(self.selected)))[0]
        done = d.progress.done
        hide = self.hide_done.isChecked()
        for i in idx:
            if hide and int(d.ids[i]) in done:
                continue
            it = MarkerItem(self, int(i))
            x, y = d.p0[i]
            it.setPos(float(x), float(y))
            self.scene.addItem(it)
            self.markers[int(i)] = it

    def toggle_point(self, i: int):
        d = self.data
        pid = int(d.ids[i])
        if pid in d.progress.done:
            d.progress.unmark(pid)
        else:
            d.progress.mark(pid)
        if self.hide_done.isChecked() and pid in d.progress.done and i in self.markers:
            self.scene.removeItem(self.markers.pop(i))
        elif i in self.markers:
            self.markers[i].update()
        self._update_counts()
        self.progress_changed.emit()

    def refresh_progress(self):
        """Appelé quand la progression change depuis l'overlay (raccourcis, panneau)."""
        if self.hide_done.isChecked():
            self._rebuild_markers()
        else:
            for it in self.markers.values():
                it.update()
        self._update_counts()

    # ------------------------------------------------------------------ joueur
    def set_player(self, p0_xy, heading=None):
        if p0_xy is None:
            self.player.setVisible(False)
            return
        if heading is not None:
            self.player.set_heading(*heading)
        self.player.setPos(float(p0_xy[0]), float(p0_xy[1]))
        self.player.setVisible(True)

    # ------------------------------------------------------------------ suivi du joueur
    def set_follow(self, on: bool, reason: str = ""):
        on = bool(on)
        if self.b_follow.isChecked() != on:
            self.b_follow.blockSignals(True)
            self.b_follow.setChecked(on)
            self.b_follow.blockSignals(False)
        self.following = on
        self.cfg["map_follow"] = on
        if on:
            self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
            if self.player.isVisible():
                s = self.view.scale_level()
                if s < 0.5:
                    f = 0.6 / s
                    self.view.scale(f, f)
                self.view.centerOn(self.player)
                self.stats.setText("📍 Suivi activé : la carte suit ton personnage.")
            else:
                self.stats.setText("📍 Suivi activé : en attente de ta position (mini-map visible en jeu).")
            self._follow_timer.start()
        else:
            self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self._follow_timer.stop()
            if reason == "moved":
                self.stats.setText("Suivi désactivé (tu as déplacé la carte). Reclique sur « Me suivre ».")
        self._schedule_tiles()

    def _follow_step(self):
        if not self.following or not self.isVisible() or not self.player.isVisible():
            return
        cur = self.view.mapToScene(self.view.viewport().rect().center())
        tgt = self.player.pos()
        dx, dy = tgt.x() - cur.x(), tgt.y() - cur.y()
        s = self.view.scale_level()
        dist_px = (dx * dx + dy * dy) ** 0.5 * s
        if dist_px < 0.6:
            return
        k = 1.0 if dist_px > self.view.viewport().width() else 0.22   # glissement fluide, saut si très loin
        self.view.centerOn(cur.x() + dx * k, cur.y() + dy * k)
        self._schedule_tiles()

    def center_on_player(self):
        if not self.player.isVisible():
            self.stats.setText("Position inconnue : lance le jeu avec la mini-map visible.")
            return
        s = self.view.scale_level()
        if s < 0.5:
            f = 0.6 / s
            self.view.scale(f, f)
        self.view.centerOn(self.player)
        self._schedule_tiles()

    def closeEvent(self, e):
        self.net.clear_queue()
        e.accept()  # la fenêtre est seulement cachée ; l'overlay continue de tourner

