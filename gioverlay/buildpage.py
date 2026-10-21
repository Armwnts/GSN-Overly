"""Page « Builds » de la carte interactive :
- liste des personnages (sephijin.fr) avec recherche,
- fiche du personnage : image(s) de synthèse du build + résumé + sections du guide,
- discussion avec Gemini (même clé API que le panneau de l'overlay).

Le contenu vient de sephijin.fr ; il est toujours affiché avec le lien vers la page d'origine.
"""
from __future__ import annotations

import html
import os
from pathlib import Path

from PyQt6.QtCore import QThread, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (QButtonGroup, QDialog, QGridLayout, QStackedWidget, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                             QScrollArea, QSizePolicy, QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from .builds import BuildError, BuildStore, guide_text

STYLE = """
QWidget#bp_side, QWidget#bp_chat { background: #16181d; }
QWidget#bp_side QLabel, QWidget#bp_chat QLabel { color: #e8e6e3; }
QWidget#bp_guide { background: #111318; }
QLabel#h { font-size: 16px; font-weight: 600; color: #f5c542; }
QLabel#name { font-size: 26px; font-weight: 700; color: #f5c542; }
QLabel#upd, QLabel#muted { color: #8b8f98; font-size: 11px; }
QLabel#summary { color: #e8e6e3; font-size: 14px; line-height: 140%;
                 background: #1b1e24; border-left: 3px solid #f5c542; border-radius: 6px; padding: 10px 12px; }
QLabel#body { color: #d9d6d1; font-size: 13px; }
QLabel#img { background: #0d0f13; border-radius: 8px; }
QListWidget { background: #1b1e24; color: #e8e6e3; border: none; font-size: 13px; }
QListWidget::item { padding: 4px 2px; }
QListWidget::item:selected { background: #2a2e37; color: #f5c542; }
QTextBrowser { background: #1b1e24; color: #e8e6e3; border: none; border-radius: 8px; padding: 6px; font-size: 13px; }
QScrollArea { border: none; background: #111318; }
QPushButton#chip { font-size: 11px; padding: 4px 8px; border-radius: 11px; }
QPushButton#link { color: #f5c542; background: transparent; border: none; text-align: left; padding: 2px 0; }
QPushButton#link:hover { text-decoration: underline; }
QSplitter::handle { background: #0f1115; }
QWidget#subnav { background: #111318; border-bottom: 1px solid #1f2229; }
QPushButton#pill { background: #1f2229; border: 1px solid #383d48; border-radius: 13px; padding: 5px 14px;
                   color: #a7abb3; font-weight: 600; }
QPushButton#pill:checked { background: #f5c542; color: #16181d; border-color: #f5c542; }
QPushButton#go { background: #f5c542; color: #16181d; border: none; font-weight: 700; padding: 7px 12px; }
QGroupBox { color: #f5c542; font-weight: 600; border: 1px solid #262a33; border-radius: 10px;
            margin-top: 14px; padding: 14px 10px 10px 10px; background: #15171c; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
QGroupBox QLabel, QCheckBox { color: #e8e6e3; }
QComboBox, QSpinBox { background: #1f2229; color: #e8e6e3; border: 1px solid #383d48; border-radius: 6px; padding: 3px 6px; }
QTableWidget { background: #15171c; color: #e8e6e3; border: none; font-size: 13px; }
QHeaderView::section { background: #15171c; color: #8b8f98; border: none; padding: 4px; }
QToolButton { background: #2a2e37; color: #e8e6e3; border: 1px solid #383d48; border-radius: 6px; padding: 3px 8px; }
QScrollBar:vertical { background: #111318; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #383d48; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""

SUGGESTIONS = ["Quelles armes ?", "Quels artéfacts et stats ?", "Quelle équipe ?", "Version F2P ?"]


class Job(QThread):
    """Exécute fn(*args) hors de l'interface ; résultat via `ok` ou `fail`."""
    ok = pyqtSignal(object)
    fail = pyqtSignal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.ok.emit(self.fn(*self.args))
        except BuildError as e:
            self.fail.emit(str(e))
        except Exception as e:  # noqa: BLE001 — on affiche l'erreur plutôt que de planter
            self.fail.emit(str(e) or type(e).__name__)


class ThumbJob(QThread):
    """Télécharge les miniatures des personnages une par une."""
    got = pyqtSignal(str, object)

    def __init__(self, store: BuildStore, chars: list[dict]):
        super().__init__()
        self.store, self.chars = store, chars

    def run(self):
        for c in self.chars:
            if self.isInterruptionRequested():
                return
            if not c.get("thumb"):
                continue
            try:
                img = _load_image(self.store.image(c["thumb"], c["slug"]))
                if not img.isNull():
                    self.got.emit(c["slug"], img.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                                        Qt.TransformationMode.SmoothTransformation))
            except Exception:  # noqa: BLE001 — une miniature manquante n'est pas grave
                continue


def _load_image(path: Path) -> QImage:
    img = QImage(str(path))
    if img.isNull():  # format non pris en charge par Qt : on passe par OpenCV
        import cv2
        a = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if a is not None:
            a = cv2.cvtColor(a, cv2.COLOR_BGR2RGB)
            img = QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format.Format_RGB888).copy()
    return img


def find_sephijin(chars: list[dict], name: str) -> dict | None:
    from .rosterpage import norm
    n = norm(name)
    for c in chars:
        if n in (norm(c["name"]), norm(c["slug"])):
            return c
    return None


def _jpeg(path: Path, side: int = 1568) -> bytes | None:
    import cv2
    a = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if a is None:
        return None
    s = side / max(a.shape[:2])
    if s < 1:
        a = cv2.resize(a, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", a, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes() if ok else None


# ---------------------------------------------------------------------------- image
class ImageLabel(QLabel):
    """Image qui s'adapte à la largeur disponible ; clic = plein écran zoomable."""
    clicked = pyqtSignal()

    def __init__(self, img: QImage):
        super().__init__()
        self.setObjectName("img")
        self.img = img
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Cliquer pour agrandir")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._w = 0

    def fit(self, width: int):
        width = max(200, min(width, self.img.width()))
        if width == self._w:
            return
        self._w = width
        pm = QPixmap.fromImage(self.img).scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(pm)
        self.setFixedHeight(pm.height())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class ImageViewer(QDialog):
    def __init__(self, img: QImage, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet("QDialog { background: #0d0f13; } QGraphicsView { border: none; background: #0d0f13; }")
        scene = QGraphicsScene(self)
        it = QGraphicsPixmapItem(QPixmap.fromImage(img))
        it.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        scene.addItem(it)
        self.view = QGraphicsView(scene)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.wheelEvent = self._wheel
        hint = QLabel("Molette : zoom · glisser : se déplacer · double-clic : ajuster")
        hint.setStyleSheet("color: #8b8f98; font-size: 11px; padding: 4px 8px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view, 1)
        lay.addWidget(hint)
        self.view.mouseDoubleClickEvent = lambda e: self._fit()
        scr = self.screen().availableGeometry() if self.screen() else None
        w = min(img.width() + 20, int(scr.width() * 0.9) if scr else 1400)
        h = min(img.height() + 40, int(scr.height() * 0.9) if scr else 900)
        self.resize(max(w, 500), max(h, 400))
        self._img = img

    def _fit(self):
        self.view.fitInView(self.view.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def showEvent(self, e):
        super().showEvent(e)
        if self._img.width() > self.view.viewport().width() or self._img.height() > self.view.viewport().height():
            self._fit()

    def _wheel(self, e):
        f = 1.0015 ** e.angleDelta().y()
        s = self.view.transform().m11() * f
        if 0.05 < s < 8:
            self.view.scale(f, f)


# ---------------------------------------------------------------------------- page
class BuildPage(QWidget):
    def __init__(self, store: BuildStore, cfg: dict, save_cfg, gamedata=None, roster=None,
                 show_on_map=None, has_map_label=None):
        super().__init__()
        self.store, self.cfg, self.save_cfg = store, cfg, save_cfg
        self.gamedata, self.roster = gamedata, roster
        self.roster_view = self.teams_view = None
        self.setStyleSheet(STYLE)
        self.chars: list[dict] = []
        self.items: dict[str, QListWidgetItem] = {}
        self.page: dict | None = None
        self.images: list[ImageLabel] = []
        self.image_paths: list[Path] = []
        self.turns: list[tuple[str, str]] = []
        self._jobs: set[QThread] = set()
        self._page_req = 0
        self._chat_req = 0
        self._loaded = False

        # --- liste des personnages -----------------------------------------------------
        side = QWidget()
        side.setObjectName("bp_side")
        side.setMinimumWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 12, 12, 12)
        h = QLabel("Personnages")
        h.setObjectName("h")
        sl.addWidget(h)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher un personnage…")
        self.search.textChanged.connect(self._filter)
        sl.addWidget(self.search)
        self.list = QListWidget()
        self.list.setIconSize(QSize(36, 36))
        self.list.setUniformItemSizes(True)
        self.list.currentItemChanged.connect(self._select)
        sl.addWidget(self.list, 1)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        sl.addWidget(self.status)
        b_ref = QPushButton("⟳ Actualiser")
        b_ref.setToolTip("Recharger la liste et la fiche depuis sephijin.fr")
        b_ref.clicked.connect(self.refresh)
        sl.addWidget(b_ref)

        # --- fiche du personnage ------------------------------------------------------
        self.guide = QWidget()
        self.guide.setObjectName("bp_guide")
        gl = QVBoxLayout(self.guide)
        gl.setContentsMargins(26, 20, 26, 26)
        gl.setSpacing(10)
        self.name = QLabel("Builds")
        self.name.setObjectName("name")
        self.name.setWordWrap(True)
        gl.addWidget(self.name)
        self.updated = QLabel()
        self.updated.setObjectName("upd")
        gl.addWidget(self.updated)
        self.summary = QLabel("Choisis un personnage dans la liste à gauche pour voir son build "
                              "(image de synthèse et résumé du guide sephijin.fr).")
        self.summary.setObjectName("summary")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        gl.addWidget(self.summary)
        self.img_box = QVBoxLayout()
        self.img_box.setSpacing(8)
        gl.addLayout(self.img_box)
        self.img_hint = QLabel()
        self.img_hint.setObjectName("muted")
        gl.addWidget(self.img_hint)
        self.body = QLabel()
        self.body.setObjectName("body")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        gl.addWidget(self.body)
        self.b_link = QPushButton("↗ Voir le guide complet sur sephijin.fr")
        self.b_link.setObjectName("link")
        self.b_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_link.clicked.connect(self._open_source)
        self.b_link.hide()
        gl.addWidget(self.b_link)
        credit = QLabel("Guides et images : sephijin.fr — tous droits réservés à leur auteur.")
        credit.setObjectName("muted")
        gl.addWidget(credit)
        gl.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.guide)

        # --- discussion Gemini ----------------------------------------------------------
        chat = QWidget()
        chat.setObjectName("bp_chat")
        chat.setMinimumWidth(300)
        cl = QVBoxLayout(chat)
        cl.setContentsMargins(12, 12, 12, 12)
        h2 = QLabel("Demander à Gemini")
        h2.setObjectName("h")
        cl.addWidget(h2)
        self.chat_ctx = QLabel()
        self.chat_ctx.setObjectName("muted")
        self.chat_ctx.setWordWrap(True)
        cl.addWidget(self.chat_ctx)
        self.history = QTextBrowser()
        self.history.setOpenExternalLinks(True)
        cl.addWidget(self.history, 1)
        chips = QGridLayout()
        chips.setSpacing(4)
        self.chips = []
        for i, s in enumerate(SUGGESTIONS):
            b = QPushButton(s)
            b.setObjectName("chip")
            b.clicked.connect(lambda _=False, q=s: self.send(q))
            chips.addWidget(b, i // 2, i % 2)
            self.chips.append(b)
        cl.addLayout(chips)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ex. : quelle arme 4★ pour ce perso ?")
        self.input.returnPressed.connect(self.send)
        self.b_send = QPushButton("Envoyer")
        self.b_send.clicked.connect(self.send)
        row.addWidget(self.input, 1)
        row.addWidget(self.b_send)
        cl.addLayout(row)
        row2 = QHBoxLayout()
        b_new = QPushButton("Nouvelle conversation")
        b_new.clicked.connect(self.new_chat)
        b_key = QPushButton("Clé API Gemini…")
        b_key.clicked.connect(self.ask_key)
        row2.addWidget(b_new)
        row2.addWidget(b_key)
        cl.addLayout(row2)

        guides = QSplitter()
        guides.addWidget(side)
        guides.addWidget(self.scroll)
        guides.setStretchFactor(1, 1)
        guides.setSizes([250, 760])
        self.sub = QStackedWidget()
        self.sub.addWidget(guides)

        # --- sous-menu : Guides | Mes persos | Équipes --------------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        subnav = QWidget()
        subnav.setObjectName("subnav")
        sn = QHBoxLayout(subnav)
        sn.setContentsMargins(12, 8, 12, 8)
        self.subtabs = QButtonGroup(self)
        labels = ["📖 Guides sephijin"] + (["⭐ Mes persos", "👥 Équipes"] if gamedata is not None else [])
        for i, lab in enumerate(labels):
            b = QPushButton(lab)
            b.setObjectName("pill")
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.subtabs.addButton(b, i)
            sn.addWidget(b)
        sn.addStretch(1)
        self.subtabs.idClicked.connect(self.show_sub)
        ll.addWidget(subnav)
        ll.addWidget(self.sub, 1)
        if gamedata is not None:
            from .rosterpage import IconCache, RosterView, TeamsView
            self.icons = IconCache(gamedata, self)
            self.roster_view = RosterView(gamedata, roster, self.icons, show_on_map, has_map_label)
            self.teams_view = TeamsView(self.roster_view, self.icons)
            self.roster_view.ask.connect(lambda q, ctx: self.send(q, player=ctx))
            self.roster_view.ask_guide.connect(lambda q, ctx, name: self.send(q, player=ctx, guide_name=name))
            self.teams_view.ask.connect(lambda q, ctx: self.send(q, player=ctx))
            self.roster_view.list.currentItemChanged.connect(lambda *_: QTimer.singleShot(0, self._update_chat_ctx))
            self.sub.addWidget(self.roster_view)
            self.sub.addWidget(self.teams_view)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(chat)
        split.setStretchFactor(0, 1)
        split.setSizes([1010, 390])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self._render_chat()

    # ------------------------------------------------------------------ utilitaires
    def _run(self, job: QThread, ok=None, fail=None):
        self._jobs.add(job)
        if ok:
            job.ok.connect(ok)
        if fail:
            job.fail.connect(fail)
        job.finished.connect(lambda j=job: self._jobs.discard(j))
        job.start()
        return job

    def show_sub(self, i: int):
        self.subtabs.button(i).setChecked(True)
        self.sub.setCurrentIndex(i)
        self._update_chat_ctx()

    def api_key(self) -> str:
        return (self.cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()

    # ------------------------------------------------------------------ liste
    def showEvent(self, e):
        super().showEvent(e)
        if not self._loaded:
            self._loaded = True
            self.load_characters()

    def load_characters(self, refresh=False):
        self.status.setText("Chargement de la liste depuis sephijin.fr…")
        self._run(Job(self.store.characters, refresh), self._chars_loaded, self._chars_failed)

    def _chars_loaded(self, chars):
        cur = self.page["url"] if self.page else None
        self.chars = chars
        self.items.clear()
        self.list.blockSignals(True)
        self.list.clear()
        for c in chars:
            it = QListWidgetItem(c["name"])
            it.setData(Qt.ItemDataRole.UserRole, c)
            it.setSizeHint(QSize(0, 42))
            self.list.addItem(it)
            self.items[c["slug"]] = it
            if c["url"] == cur:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        self._filter(self.search.text())
        self.status.setText(f"{len(chars)} personnages · source : sephijin.fr")
        self._run(ThumbJob(self.store, chars)).got.connect(self._thumb)

    def _chars_failed(self, msg):
        self.status.setText(f"⚠ {msg}\nClique sur « Actualiser » pour réessayer.")

    def _thumb(self, slug, img):
        it = self.items.get(slug)
        if it is not None:
            it.setIcon(QIcon(QPixmap.fromImage(img)))

    def _filter(self, text):
        t = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def refresh(self):
        self.load_characters(refresh=True)
        if self.page:
            c = next((c for c in self.chars if c["url"] == self.page["url"]), None)
            if c:
                self.show_character(c, refresh=True)

    # ------------------------------------------------------------------ fiche
    def _select(self, cur, _prev):
        if cur is not None:
            self.show_character(cur.data(Qt.ItemDataRole.UserRole))

    def show_character(self, char: dict, refresh=False):
        self._page_req += 1
        req = self._page_req
        self.name.setText(char["name"])
        self.updated.setText("")
        self.summary.setText("Chargement du guide…")
        self._clear_images()
        self.body.setText("")
        self.b_link.hide()
        self._run(Job(self.store.page, char, refresh),
                  lambda pg, r=req: self._page_loaded(r, pg),
                  lambda msg, r=req: self._page_failed(r, msg))

    def _page_failed(self, req, msg):
        if req == self._page_req:
            self.summary.setText(f"⚠ {msg}")

    def _page_loaded(self, req, pg):
        if req != self._page_req:
            return
        if self.turns and (not self.page or self.page["url"] != pg["url"]):
            self.turns.append(("info", f"Personnage affiché : {pg['name']}"))
            self._render_chat()
        self.page = pg
        self.name.setText(pg["name"])
        self.updated.setText(pg.get("updated", ""))
        self.summary.setText("\n\n".join(pg.get("summary") or []) or "(Pas de résumé sur la page.)")
        parts = []
        for s in pg.get("sections", []):
            parts.append(f"<h3 style='color:#f5c542; margin-top:14px'>{html.escape(s['title'])}</h3>")
            for ln in s["lines"]:
                t = html.escape(ln).replace("\n", "<br>")
                parts.append(f"<p style='margin:4px 0'><b>{t}</b></p>" if ln.startswith("▸")
                             else f"<p style='margin:4px 0'>{t}</p>")
        self.body.setText("".join(parts))
        self.b_link.show()
        self.img_hint.setText("Chargement de l'image du build…" if pg.get("synth") else
                              "Pas d'image de synthèse trouvée sur cette page.")
        slug = pg["url"].rstrip("/").rsplit("/", 1)[-1]
        self._run(Job(self._fetch_images, pg.get("synth", []), slug),
                  lambda res, r=req: self._images_loaded(r, res),
                  lambda msg, r=req: r == self._page_req and self.img_hint.setText(f"⚠ Image : {msg}"))
        self._update_chat_ctx()

    def _fetch_images(self, urls, slug):
        out = []
        for u in urls:
            p = self.store.image(u, slug)
            img = _load_image(p)
            if not img.isNull():
                out.append((p, img))
        return out

    def _clear_images(self):
        for lab in self.images:
            lab.setParent(None)
            lab.deleteLater()
        self.images, self.image_paths = [], []
        self.img_hint.setText("")

    def _images_loaded(self, req, res):
        if req != self._page_req:
            return
        self._clear_images()
        for path, img in res:
            lab = ImageLabel(img)
            lab.clicked.connect(lambda im=img: self._zoom(im))
            self.img_box.addWidget(lab)
            self.images.append(lab)
            self.image_paths.append(path)
        self.img_hint.setText("Clique sur l'image pour l'agrandir." if res else
                              "Image du build introuvable.")
        self._fit_images()

    def _fit_images(self):
        w = self.scroll.viewport().width() - 52
        for lab in self.images:
            lab.fit(w)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_images()

    def _zoom(self, img):
        dlg = ImageViewer(img, f"Build — {self.page['name'] if self.page else ''}", self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()

    def _open_source(self):
        if self.page:
            QDesktopServices.openUrl(QUrl(self.page["url"]))

    # ------------------------------------------------------------------ Gemini
    def _update_chat_ctx(self):
        if not self.api_key():
            self.chat_ctx.setText("⚠ Pas de clé API Gemini : clique sur « Clé API Gemini… » "
                                  "(c'est la même que dans le panneau de l'overlay).")
        elif self.sub.currentIndex() == 1 and self.roster_view is not None:
            c = self.roster_view.cur
            self.chat_ctx.setText(f"Gemini connaît ta progression et ton équipement de {c['name']}." if c else
                                  "Choisis un de tes persos : Gemini tiendra compte de son niveau et de son équipement.")
        elif self.sub.currentIndex() == 2:
            self.chat_ctx.setText("Gemini connaît la liste de tes persos pour proposer des équipes.")
        elif self.page:
            self.chat_ctx.setText(f"Gemini lit le guide sephijin.fr de {self.page['name']} pour te répondre.")
        else:
            self.chat_ctx.setText("Choisis un personnage pour que Gemini s'appuie sur son guide, "
                                  "ou pose une question générale.")

    def _render_chat(self):
        if not self.turns:
            md = ("*Pose une question sur le build du personnage affiché : armes, artéfacts, "
                  "statistiques, équipes, alternatives sans 5★…*")
        else:
            blocks = []
            for role, text in self.turns:
                if role == "user":
                    blocks.append(f"**🧑 Toi :** {text}")
                elif role == "model":
                    blocks.append(f"**✨ Gemini :**\n\n{text}")
                else:  # message de l'application (attente, erreur, changement de perso)
                    blocks.append(f"*{text}*")
            md = "\n\n---\n\n".join(blocks)
        self.history.setMarkdown(md)
        sb = self.history.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._update_chat_ctx()

    def send(self, text=None, player=None, guide_name=None):
        q = (text if isinstance(text, str) else self.input.text()).strip()
        if not q or self.b_send.isEnabled() is False:
            return
        key = self.api_key()
        if not key:
            self.ask_key()
            key = self.api_key()
            if not key:
                return
        self.input.clear()
        self.turns.append(("user", q))
        self._set_busy(True)
        self.turns.append(("info", "Gemini réfléchit…"))
        self._render_chat()
        convo = [(r, t) for r, t in self.turns if r in ("user", "model")]
        tab = self.sub.currentIndex()
        guide, img_path = "", None
        if player is None:   # contexte selon la sous-page affichée
            if tab == 1 and self.roster_view is not None:
                player = self.roster_view.player_context()
                guide_name = self.roster_view.cur["name"] if self.roster_view.cur else None
            elif tab == 2 and self.teams_view is not None:
                player = self.teams_view.context()
            else:
                player = ""
        if tab == 0 and self.page and not guide_name:
            guide = guide_text(self.page)
            img_path = self.image_paths[0] if self.image_paths else None
        self._chat_req += 1
        req = self._chat_req
        self._run(Job(self._ask_gemini, key, convo, guide, img_path, player, guide_name),
                  lambda ans, r=req: self._answer(r, ans),
                  lambda msg, r=req: self._answer(r, None, msg))

    def _ask_gemini(self, key, convo, guide, img_path, player, guide_name):
        from .quest_gemini import build_chat
        if guide_name and not guide:   # guide sephijin du perso (s'il existe), lu en arrière-plan
            try:
                char = find_sephijin(self.chars or self.store.characters(), guide_name)
                if char:
                    pg = self.store.page(char)
                    guide = guide_text(pg)
                    if pg.get("synth"):
                        img_path = self.store.image(pg["synth"][0], char["slug"])
            except Exception:  # noqa: BLE001 — sans guide, Gemini répond quand même
                pass
        img = _jpeg(img_path) if img_path else None
        return build_chat(key, convo, guide, img, player=player)

    def _answer(self, req, ans, err=None):
        if req != self._chat_req:
            return
        if self.turns and self.turns[-1][0] == "info":
            self.turns.pop()
        if ans:
            self.turns.append(("model", ans))
        else:
            # la question sans réponse est retirée de l'historique envoyé à Gemini
            q = self.turns.pop()[1] if self.turns and self.turns[-1][0] == "user" else ""
            self.turns.append(("info", f"⚠ {err}"))
            if q:
                self.input.setText(q)
        self._set_busy(False)
        self._render_chat()

    def _set_busy(self, busy):
        self.b_send.setEnabled(not busy)
        for b in self.chips:
            b.setEnabled(not busy)
        self.b_send.setText("…" if busy else "Envoyer")

    def new_chat(self):
        self._chat_req += 1
        self.turns = []
        self._set_busy(False)
        self._render_chat()

    def ask_key(self):
        key, ok = QInputDialog.getText(
            self, "Clé API Gemini",
            "Colle ta clé API Gemini (gratuite sur aistudio.google.com).\n"
            "C'est la même clé que dans le panneau de l'overlay.",
            QLineEdit.EchoMode.Password, self.cfg.get("gemini_api_key", ""))
        if ok:
            self.cfg["gemini_api_key"] = key.strip()
            self.save_cfg()
        self._update_chat_ctx()
