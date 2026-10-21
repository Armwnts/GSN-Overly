"""Sous-sections de Builds : « Mes persos » (progression, matériaux à farmer, équipement) et « Équipes »."""
from __future__ import annotations

import re
import unicodedata

from PyQt6.QtCore import QSize, QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QSplitter,
                             QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from .gamedata import ELEMENT_COLORS, MORA_ID, PHASE_LABELS, active_talents, item_info, needs
from .uikit import Job, JobOwner, load_image

RANK_COLORS = {1: "#8b8f98", 2: "#7fb069", 3: "#5b9bd5", 4: "#a77fd1", 5: "#f5c542"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if unicodedata.category(c) != "Mn").lower())


class IconJob(QThread):
    got = pyqtSignal(str, object)

    def __init__(self, gamedata, icons: list[str], size=48):
        super().__init__()
        self.gd, self.icons, self.size = gamedata, icons, size

    def run(self):
        for ic in dict.fromkeys(self.icons):
            if self.isInterruptionRequested():
                return
            try:
                p = self.gd.icon(ic)
                if p:
                    img = load_image(p)
                    if not img.isNull():
                        self.got.emit(ic, img.scaled(self.size, self.size, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))
            except Exception:  # noqa: BLE001
                continue


class IconCache(JobOwner):
    """Icônes (personnages et matériaux) téléchargées une fois, partagées par les vues."""

    def __init__(self, gamedata, parent):
        self.gd = gamedata
        self.pix: dict[str, QPixmap] = {}
        self.waiting: dict[str, list] = {}
        self._queue: list[str] = []
        self._timer = QTimer(parent, singleShot=True, interval=30, timeout=self._flush)

    def want(self, icon: str, apply):
        if not icon:
            return
        if icon in self.pix:
            apply(self.pix[icon])
            return
        first = icon not in self.waiting
        self.waiting.setdefault(icon, []).append(apply)
        if first:
            self._queue.append(icon)
            self._timer.start()

    def _flush(self):
        q, self._queue = self._queue, []
        if q:
            job = IconJob(self.gd, q, 96)
            job.got.connect(self._got)
            self._run(job)

    def _got(self, icon, img):
        pm = QPixmap.fromImage(img)
        self.pix[icon] = pm
        for apply in self.waiting.pop(icon, []):
            try:
                apply(pm)
            except RuntimeError:  # widget détruit entre-temps
                pass


# ============================================================================ Mes persos
class RosterView(QWidget, JobOwner):
    ask = pyqtSignal(str, str)          # (question, contexte joueur) -> discussion Gemini
    ask_guide = pyqtSignal(str, str, str)  # (question, contexte, nom du perso pour le guide sephijin)
    import_requested = pyqtSignal()        # « Importer par UID »

    def __init__(self, gamedata, roster, icons: IconCache, show_on_map=None, has_map_label=None):
        super().__init__()
        self.gd, self.roster, self.icons = gamedata, roster, icons
        self.show_on_map = show_on_map
        self.has_map_label = has_map_label or (lambda name: False)
        self.chars: list[dict] = []
        self.items: dict[str, QListWidgetItem] = {}
        self.cur: dict | None = None
        self.detail: dict | None = None
        self._loaded = False
        self._req = 0
        self._building = False
        self.details_cache: dict[str, dict] = {}

        # --- liste ---------------------------------------------------------------------
        side = QWidget()
        side.setObjectName("bp_side")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 12, 12, 12)
        h = QLabel("Tous les personnages")
        h.setObjectName("h")
        sl.addWidget(h)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher…")
        self.search.textChanged.connect(self._filter)
        sl.addWidget(self.search)
        self.only_mine = QCheckBox("Seulement mes persos")
        self.only_mine.toggled.connect(lambda: self._filter(self.search.text()))
        sl.addWidget(self.only_mine)
        self.list = QListWidget()
        self.list.setIconSize(QSize(36, 36))
        self.list.setUniformItemSizes(True)
        self.list.currentItemChanged.connect(self._select)
        sl.addWidget(self.list, 1)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        sl.addWidget(self.status)
        b_all = QPushButton("📦 Tout mon farm")
        b_all.setToolTip("Matériaux qu'il te reste à farmer pour tous tes persos et leurs objectifs")
        b_all.clicked.connect(self.show_all_farm)
        sl.addWidget(b_all)
        b_imp = QPushButton("⬇ Importer mes persos (UID)")
        b_imp.setToolTip("Récupère automatiquement les persos de ta vitrine en jeu (niveau, aptitudes, équipement)")
        b_imp.clicked.connect(self.import_requested)
        sl.addWidget(b_imp)

        # --- fiche ------------------------------------------------------------------------
        self.body = QWidget()
        self.body.setObjectName("bp_guide")
        self.bl = QVBoxLayout(self.body)
        self.bl.setContentsMargins(24, 18, 24, 24)
        self.bl.setSpacing(12)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.body)
        self._placeholder("Choisis un personnage à gauche, puis coche « J'ai ce personnage ».\n\n"
                          "Tu pourras indiquer son niveau, ses aptitudes et ton objectif : l'app calcule "
                          "les matériaux qu'il te reste à farmer et te montre où les trouver sur la carte.")

        split = QSplitter()
        split.addWidget(side)
        split.addWidget(self.scroll)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 760])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

    # ------------------------------------------------------------------ liste
    def showEvent(self, e):
        super().showEvent(e)
        if not self._loaded:
            self._loaded = True
            self.load()

    def load(self, refresh=False):
        self.status.setText("Chargement des personnages…")
        self._run(Job(self.gd.characters, refresh), self._loaded_chars,
                  lambda m: self.status.setText(f"⚠ {m}"))

    def _loaded_chars(self, chars):
        self.chars = chars
        self.items.clear()
        self.list.blockSignals(True)
        self.list.clear()
        for c in chars:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, c)
            it.setSizeHint(QSize(0, 42))
            self.list.addItem(it)
            self.items[c["id"]] = it
            self._refresh_item(c["id"])
            self.icons.want(c["icon"], lambda pm, it=it: it.setIcon(QIcon(pm)))
        self.list.blockSignals(False)
        self._filter(self.search.text())
        self._update_status()

    def _update_status(self):
        n = sum(1 for c in self.chars if self.roster.is_owned(c["id"]))
        self.status.setText(f"{n} perso(s) débloqué(s) sur {len(self.chars)} · données : Project Yatta")

    def _refresh_item(self, cid):
        it = self.items.get(cid)
        if it is None:
            return
        c = it.data(Qt.ItemDataRole.UserRole)
        owned = self.roster.is_owned(cid)
        it.setText(("⭐ " if owned else "") + c["name"])
        f = it.font()
        f.setBold(owned)
        it.setFont(f)

    def _filter(self, text):
        t = norm(text)
        mine = self.only_mine.isChecked()
        for i in range(self.list.count()):
            it = self.list.item(i)
            c = it.data(Qt.ItemDataRole.UserRole)
            it.setHidden(bool(t) and t not in norm(c["name"]) or (mine and not self.roster.is_owned(c["id"])))

    def reload_roster(self):
        """« Mes persos » a changé ailleurs (import par UID) : on rafraîchit l'affichage."""
        for cid in self.items:
            self._refresh_item(cid)
        self._update_status()
        self._filter(self.search.text())
        if self.cur is not None:
            self.show_char(self.cur)

    def owned_chars(self) -> list[dict]:
        return [c for c in self.chars if self.roster.is_owned(c["id"])]

    # ------------------------------------------------------------------ fiche
    def _clear(self):
        while self.bl.count():
            it = self.bl.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
            elif it.layout():
                _drop_layout(it.layout())

    def _placeholder(self, text):
        self._clear()
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setObjectName("summary")
        self.bl.addWidget(lab)
        self.bl.addStretch(1)

    def _select(self, cur, _prev):
        if cur is not None:
            self.show_char(cur.data(Qt.ItemDataRole.UserRole))

    def show_char(self, c):
        self.cur = c
        self.detail = None
        self._req += 1
        req = self._req
        self._clear()
        self._header(c)
        wait = QLabel("Chargement des matériaux…")
        wait.setObjectName("muted")
        self.bl.addWidget(wait)
        self.bl.addStretch(1)
        self._run(Job(self.gd.detail, c["id"]), lambda d, r=req: self._detail_loaded(r, d),
                  lambda m, r=req: r == self._req and wait.setText(f"⚠ {m}"))

    def _header(self, c):
        row = QHBoxLayout()
        ic = QLabel()
        ic.setFixedSize(84, 84)
        ic.setStyleSheet(f"background: #1b1e24; border-radius: 42px; border: 2px solid "
                         f"{ELEMENT_COLORS.get(c['element'], '#383d48')};")
        self.icons.want(c["icon"], lambda pm, ic=ic: ic.setPixmap(pm.scaled(
            80, 80, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
        row.addWidget(ic)
        v = QVBoxLayout()
        n = QLabel(c["name"])
        n.setObjectName("name")
        v.addWidget(n)
        col = ELEMENT_COLORS.get(c["element"], "#a7abb3")
        sub = QLabel(f"<span style='color:#f5c542'>{'★' * c['rank']}</span>  ·  "
                     f"<span style='color:{col}'>{c['element']}</span>  ·  {c['weapon']}")
        sub.setStyleSheet("font-size: 13px; color: #c9c6c1;")
        v.addWidget(sub)
        self.owned_box = QCheckBox("J'ai ce personnage")
        self.owned_box.setStyleSheet("font-size: 14px; font-weight: 600; color: #e8e6e3;")
        self.owned_box.setChecked(self.roster.is_owned(c["id"]))
        self.owned_box.toggled.connect(self._owned_toggled)
        v.addWidget(self.owned_box)
        row.addLayout(v, 1)
        self.bl.addLayout(row)

    def _owned_toggled(self, on):
        if not self.cur:
            return
        self.roster.set_owned(self.cur["id"], on)
        self._refresh_item(self.cur["id"])
        self._update_status()
        self._filter(self.search.text())
        if self.detail is not None:
            self._build_body()

    def _detail_loaded(self, req, d):
        if req != self._req:
            return
        self.detail = d
        self.details_cache[self.cur["id"]] = d
        self._build_body()

    def _build_body(self):
        c, d = self.cur, self.detail
        # on garde l'en-tête (premier élément), on reconstruit le reste
        while self.bl.count() > 1:
            it = self.bl.takeAt(1)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
            elif it.layout():
                _drop_layout(it.layout())
        owned = self.roster.is_owned(c["id"])
        st = self.roster.get(c["id"])
        talents = active_talents(d)

        # --- progression ---------------------------------------------------------------
        g = QGroupBox("Progression et objectif")
        gl = QGridLayout(g)
        gl.setColumnStretch(4, 1)
        self._building = True
        gl.addWidget(QLabel("Niveau"), 0, 0)
        self.ph_cur, self.ph_tgt = QComboBox(), QComboBox()
        for cb, val in ((self.ph_cur, st["phase"]), (self.ph_tgt, st["target_phase"])):
            cb.addItems(PHASE_LABELS)
            cb.setCurrentIndex(int(val))
            cb.currentIndexChanged.connect(self._changed)
        gl.addWidget(self.ph_cur, 0, 1)
        gl.addWidget(QLabel("→"), 0, 2)
        gl.addWidget(self.ph_tgt, 0, 3)
        self.t_cur, self.t_tgt = [], []
        for i, t in enumerate(talents):
            name = QLabel(t.get("name", f"Aptitude {i + 1}"))
            name.setToolTip(["Attaque normale", "Compétence élémentaire", "Déchaînement élémentaire"][i]
                            if i < 3 else "")
            gl.addWidget(name, i + 1, 0)
            a, b = QSpinBox(), QSpinBox()
            for sb, val in ((a, st["talents"][i]), (b, st["talent_targets"][i])):
                sb.setRange(1, 10)
                sb.setValue(int(val))
                sb.valueChanged.connect(self._changed)
            gl.addWidget(a, i + 1, 1)
            gl.addWidget(QLabel("→"), i + 1, 2)
            gl.addWidget(b, i + 1, 3)
            self.t_cur.append(a)
            self.t_tgt.append(b)
        gl.addWidget(QLabel("Constellation"), 5, 0)
        self.cons = QSpinBox()
        self.cons.setRange(0, 6)
        self.cons.setPrefix("C")
        self.cons.setValue(int(st["cons"]))
        self.cons.valueChanged.connect(self._changed)
        gl.addWidget(self.cons, 5, 1)
        quick = QPushButton("Objectif conseillé (90 · 8/8/8)")
        quick.clicked.connect(self._suggested_goal)
        gl.addWidget(quick, 5, 3)
        self._building = False
        g.setEnabled(owned)
        self.bl.addWidget(g)

        # --- matériaux ----------------------------------------------------------------
        self.mat_group = QGroupBox("Matériaux qu'il te reste à farmer")
        ml = QVBoxLayout(self.mat_group)
        self.mat_summary = QLabel()
        self.mat_summary.setWordWrap(True)
        ml.addWidget(self.mat_summary)
        self.table = MaterialTable(self.icons, self.roster, self.show_on_map, self.has_map_label,
                                   lambda name: self.ask.emit(
                                       f"Où et comment farmer « {name} » dans Genshin Impact ? "
                                       "(domaine et jours, boss ou ennemis, région, astuces)", ""))
        self.table.inventory_changed.connect(self._refresh_summary)
        ml.addWidget(self.table)
        hint = QLabel("« J'ai » = ce que tu possèdes déjà (commun à tous tes persos). "
                      "Hors livres d'EXP et matériaux d'arme.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        ml.addWidget(hint)
        self.mat_group.setVisible(owned)
        self.bl.addWidget(self.mat_group)

        # --- équipement -----------------------------------------------------------------
        eq = QGroupBox("Mon équipement")
        el = QGridLayout(eq)
        self.f_weapon = QLineEdit(st["weapon"])
        self.f_weapon.setPlaceholderText("ex. : Cinabre fusiforme R1, niv. 90")
        self.f_art = QLineEdit(st["artifacts"])
        self.f_art.setPlaceholderText("ex. : 4 pièces Troupe dorée ; sablier DÉF, coupe Géo, couronne CRIT")
        self.f_stats = QLineEdit(st["stats"])
        self.f_stats.setPlaceholderText("ex. : DÉF 2400, Taux CRIT 62 %, DGT CRIT 140 %, Recharge 120 %")
        self.f_notes = QLineEdit(st["notes"])
        self.f_notes.setPlaceholderText("Remarques (équipe actuelle, problèmes…)")
        for i, (lab, f) in enumerate((("Arme", self.f_weapon), ("Artéfacts", self.f_art),
                                      ("Stats", self.f_stats), ("Notes", self.f_notes))):
            el.addWidget(QLabel(lab), i, 0)
            el.addWidget(f, i, 1)
            f.editingFinished.connect(self._changed)
        b = QPushButton("💬 Analyser mon build avec Gemini")
        b.setObjectName("go")
        b.setToolTip("Gemini compare ton équipement avec le guide sephijin.fr de ce personnage")
        b.clicked.connect(self._analyse)
        el.addWidget(b, 4, 1)
        eq.setEnabled(owned)
        self.bl.addWidget(eq)
        if not owned:
            tip = QLabel("Coche « J'ai ce personnage » pour suivre sa progression et ses matériaux.")
            tip.setObjectName("muted")
            self.bl.addWidget(tip)
        self.bl.addStretch(1)
        self._refresh_mats()

    def _suggested_goal(self):
        self._building = True
        self.ph_tgt.setCurrentIndex(6)
        for sb in self.t_tgt:
            sb.setValue(max(8, sb.value()))
        self._building = False
        self._changed()

    def _changed(self, *_):
        if self._building or not self.cur or not self.roster.is_owned(self.cur["id"]):
            return
        self.roster.update(self.cur["id"], phase=self.ph_cur.currentIndex(), target_phase=self.ph_tgt.currentIndex(),
                           talents=[s.value() for s in self.t_cur], talent_targets=[s.value() for s in self.t_tgt],
                           cons=self.cons.value(), weapon=self.f_weapon.text().strip(),
                           artifacts=self.f_art.text().strip(), stats=self.f_stats.text().strip(),
                           notes=self.f_notes.text().strip())
        self._refresh_mats()

    def _refresh_mats(self):
        if not self.cur or not self.roster.is_owned(self.cur["id"]) or self.detail is None:
            return
        st = self.roster.get(self.cur["id"])
        need = needs(self.detail, st["phase"], st["target_phase"], st["talents"], st["talent_targets"])
        rows = [(item_info(self.detail, k), v) for k, v in need.items()]
        self.table.fill(rows)
        self._refresh_summary()

    def _refresh_summary(self):
        left, total = self.table.remaining()
        if total == 0:
            self.mat_summary.setText("✅ Objectif atteint : rien à farmer pour ce perso.")
        elif left == 0:
            self.mat_summary.setText("✅ Tu as déjà tout ce qu'il faut : il ne reste qu'à monter le perso !")
        else:
            self.mat_summary.setText(f"Encore {left} type(s) de matériaux à farmer sur {total}.")

    # ------------------------------------------------------------------ tout mon farm
    def show_all_farm(self):
        owned = self.owned_chars()
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.setCurrentItem(None)
        self.list.blockSignals(False)
        self.cur = None
        if not owned:
            self._placeholder("Tu n'as encore indiqué aucun personnage.\n\nChoisis un perso à gauche puis "
                              "coche « J'ai ce personnage ».")
            return
        self._req += 1
        req = self._req
        self._placeholder("Calcul des matériaux pour tous tes persos…")

        def compute():
            tot, info, who = {}, {}, {}
            for c in owned:
                d = self.gd.detail(c["id"])
                st = self.roster.get(c["id"])
                for k, v in needs(d, st["phase"], st["target_phase"], st["talents"], st["talent_targets"]).items():
                    tot[k] = tot.get(k, 0) + v
                    info.setdefault(k, item_info(d, k))
                    who.setdefault(k, []).append(c["name"])
            return tot, info, who
        self._run(Job(compute), lambda res, r=req: self._all_loaded(r, res),
                  lambda m, r=req: r == self._req and self._placeholder(f"⚠ {m}"))

    def _all_loaded(self, req, res):
        if req != self._req:
            return
        tot, info, who = res
        self._clear()
        n = QLabel("📦 Tout mon farm")
        n.setObjectName("name")
        self.bl.addWidget(n)
        sub = QLabel(f"Pour atteindre les objectifs de tes {len(self.owned_chars())} persos.")
        sub.setObjectName("muted")
        self.bl.addWidget(sub)
        self.mat_summary = QLabel()
        self.mat_summary.setStyleSheet("color: #e8e6e3; font-size: 13px;")
        self.bl.addWidget(self.mat_summary)
        self.table = MaterialTable(self.icons, self.roster, self.show_on_map, self.has_map_label,
                                   lambda name: self.ask.emit(f"Où et comment farmer « {name} » dans Genshin Impact ?", ""))
        self.table.inventory_changed.connect(self._refresh_summary)
        self.table.fill([(info[k], v) for k, v in tot.items()], who)
        self.bl.addWidget(self.table)
        b = QPushButton("💬 Demander à Gemini un planning de farm")
        b.clicked.connect(lambda: self.ask.emit(
            "Propose-moi un planning de farm efficace pour la semaine (résine, domaines selon les jours, boss) "
            "avec les matériaux qu'il me reste.", self.farm_context(tot, info)))
        self.bl.addWidget(b)
        self.bl.addStretch(1)
        self._refresh_summary()

    def farm_context(self, tot, info) -> str:
        lines = []
        for k, v in tot.items():
            left = max(0, v - self.roster.inventory.get(k, 0))
            if left:
                lines.append(f"- {info[k]['name']} : il en manque {left}")
        return "Matériaux qu'il reste au joueur à farmer :\n" + "\n".join(lines[:60])

    # ------------------------------------------------------------------ Gemini
    def player_context(self) -> str:
        if not self.cur:
            return ""
        c = self.cur
        lines = [f"Personnage regardé par le joueur : {c['name']} ({c['rank']}★, {c['element']}, {c['weapon']})."]
        if self.roster.is_owned(c["id"]):
            st = self.roster.get(c["id"])
            names = [t.get("name", "?") for t in active_talents(self.detail or {})]
            tal = ", ".join(f"{n} {v}" for n, v in zip(names, st["talents"]))
            lines.append(f"Le joueur l'a : {PHASE_LABELS[st['phase']]}, C{st['cons']}, aptitudes {tal}.")
            for lab, k in (("Arme", "weapon"), ("Artéfacts", "artifacts"), ("Stats", "stats"), ("Notes", "notes")):
                if st[k]:
                    lines.append(f"{lab} : {st[k]}")
        else:
            lines.append("Le joueur ne l'a pas (encore).")
        return "\n".join(lines)

    def _analyse(self):
        self._changed()
        if self.cur:
            self.ask_guide.emit(
                "Analyse mon build actuel de ce personnage : compare mon arme, mes artéfacts et mes stats "
                "avec le guide, dis ce qui va bien et les 3 améliorations prioritaires.",
                self.player_context(), self.cur["name"])


def _drop_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w:
            w.setParent(None)
            w.deleteLater()
        elif it.layout():
            _drop_layout(it.layout())


class MaterialTable(QTableWidget):
    inventory_changed = pyqtSignal()

    def __init__(self, icons, roster, show_on_map, has_map_label, ask_where):
        super().__init__(0, 5)
        self.icons, self.roster = icons, roster
        self.show_on_map, self.has_map_label, self.ask_where = show_on_map, has_map_label, ask_where
        self.setHorizontalHeaderLabels(["Matériau", "Il faut", "J'ai", "Reste", ""])
        self.verticalHeader().setVisible(False)
        self.setIconSize(QSize(30, 30))
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setShowGrid(False)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 5):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.rows: list[tuple[str, int]] = []
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def fill(self, rows, who: dict | None = None):
        # Mora en dernier ; le reste par rareté puis par nom
        rows = sorted(rows, key=lambda r: (r[0]["id"] == MORA_ID, -r[0]["rank"], r[0]["name"]))
        self.rows = [(info["id"], need) for info, need in rows]
        self.setRowCount(0)   # supprime aussi les anciens boutons et compteurs
        self.setRowCount(len(rows))
        for i, (info, need) in enumerate(rows):
            it = QTableWidgetItem(info["name"])
            it.setForeground(Qt.GlobalColor.white)
            if who and info["id"] in who:
                it.setToolTip("Pour : " + ", ".join(who[info["id"]]))
            self.setItem(i, 0, it)
            self.icons.want(info["icon"], lambda pm, it=it: it.setIcon(QIcon(pm)))
            n = QTableWidgetItem(f"{need:,}".replace(",", " "))
            n.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(i, 1, n)
            have = QSpinBox()
            have.setRange(0, 99_999_999)
            have.setSingleStep(1000 if info["id"] == MORA_ID else 1)
            have.setValue(self.roster.inventory.get(info["id"], 0))
            have.valueChanged.connect(lambda v, iid=info["id"], row=i: self._have(iid, v, row))
            self.setCellWidget(i, 2, have)
            self.setItem(i, 3, QTableWidgetItem())
            self._update_left(i)
            if info["id"] != MORA_ID:
                b = QToolButton()
                if self.show_on_map and self.has_map_label(info["name"]):
                    b.setText("📍 Carte")
                    b.setToolTip("Afficher ce matériau sur la carte interactive")
                    b.clicked.connect(lambda _=False, nm=info["name"]: self.show_on_map(nm))
                else:
                    b.setText("💬 Où ?")
                    b.setToolTip("Demander à Gemini où farmer ce matériau")
                    b.clicked.connect(lambda _=False, nm=info["name"]: self.ask_where(nm))
                self.setCellWidget(i, 4, b)
            self.setRowHeight(i, 38)
        self.setFixedHeight(self.horizontalHeader().height() + 38 * len(rows) + 4)

    def _have(self, iid, v, row):
        if v:
            self.roster.inventory[iid] = v
        else:
            self.roster.inventory.pop(iid, None)
        self.roster.save()
        self._update_left(row)
        self.inventory_changed.emit()

    def _update_left(self, row):
        iid, need = self.rows[row]
        left = max(0, need - self.roster.inventory.get(iid, 0))
        it = self.item(row, 3)
        it.setText("✓" if left == 0 else f"{left:,}".replace(",", " "))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        it.setForeground(Qt.GlobalColor.green if left == 0 else Qt.GlobalColor.yellow)

    def remaining(self) -> tuple[int, int]:
        left = sum(1 for iid, need in self.rows if need > self.roster.inventory.get(iid, 0))
        return left, len(self.rows)


# ============================================================================ Équipes
GOALS = ["Abîme spiral (2 équipes)", "Théâtre Imaginarium", "Exploration du monde ouvert",
         "Boss et combats difficiles", "Une équipe autour d'un perso"]


class TeamsView(QWidget):
    ask = pyqtSignal(str, str)

    def __init__(self, roster_view: RosterView, icons: IconCache):
        super().__init__()
        self.rv, self.icons = roster_view, icons
        self.setObjectName("bp_guide")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QLabel { color: #e8e6e3; } QScrollArea, QWidget#grid { background: transparent; border: none; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        n = QLabel("Constructeur d'équipes")
        n.setObjectName("name")
        lay.addWidget(n)
        sub = QLabel("Gemini propose des équipes avec les persos que tu possèdes (renseignés dans « Mes persos »). "
                     "Décoche ceux que tu ne veux pas utiliser.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #c9c6c1;")
        lay.addWidget(sub)
        form = QGridLayout()
        form.addWidget(QLabel("Objectif"), 0, 0)
        self.goal = QComboBox()
        self.goal.addItems(GOALS)
        form.addWidget(self.goal, 0, 1)
        form.addWidget(QLabel("Perso principal"), 1, 0)
        self.main = QComboBox()
        form.addWidget(self.main, 1, 1)
        form.setColumnStretch(2, 1)
        lay.addLayout(form)
        self.grid_box = QWidget()
        self.grid_box.setObjectName("grid")
        self.grid = QGridLayout(self.grid_box)
        self.grid.setSpacing(6)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.grid_box)
        lay.addWidget(sc, 1)
        row = QHBoxLayout()
        self.count = QLabel()
        self.count.setObjectName("muted")
        row.addWidget(self.count)
        row.addStretch(1)
        b = QPushButton("✨ Proposer des équipes")
        b.setObjectName("go")
        b.clicked.connect(self.propose)
        row.addWidget(b)
        lay.addLayout(row)
        self.buttons: dict[str, QToolButton] = {}

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()

    def refresh(self):
        if not self.rv.chars and not self.rv._loaded:
            self.rv._loaded = True
            self.rv.load()
            QTimer.singleShot(1500, self.refresh)
        owned = self.rv.owned_chars()
        keep = {cid for cid, b in self.buttons.items() if not b.isChecked()}
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.buttons = {}
        cur_main = self.main.currentText()
        self.main.clear()
        self.main.addItem("(au choix de Gemini)")
        if not owned:
            lab = QLabel("Aucun perso renseigné : va dans « ⭐ Mes persos » et coche ceux que tu as.")
            lab.setStyleSheet("color: #e0a84a;")
            self.grid.addWidget(lab, 0, 0)
        cols = 6
        for i, c in enumerate(owned):
            b = QToolButton()
            b.setCheckable(True)
            b.setChecked(c["id"] not in keep)
            b.setText(c["name"])
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(56, 56))
            b.setFixedSize(104, 92)
            col = ELEMENT_COLORS.get(c["element"], "#383d48")
            b.setStyleSheet(f"QToolButton {{ background: #1b1e24; color: #8b8f98; border: 1px solid #262a33; "
                            f"border-radius: 10px; font-size: 11px; }} QToolButton:checked {{ color: #e8e6e3; "
                            f"border: 2px solid {col}; background: #20242c; }}")
            b.toggled.connect(self._update_count)
            self.icons.want(c["icon"], lambda pm, b=b: b.setIcon(QIcon(pm)))
            self.grid.addWidget(b, i // cols, i % cols)
            self.buttons[c["id"]] = b
            self.main.addItem(c["name"], c["id"])
        if cur_main:
            i = self.main.findText(cur_main)
            if i >= 0:
                self.main.setCurrentIndex(i)
        self.grid.setRowStretch(len(owned) // cols + 1, 1)
        self.grid.setColumnStretch(cols, 1)
        self._update_count()

    def _update_count(self, *_):
        n = sum(1 for b in self.buttons.values() if b.isChecked())
        self.count.setText(f"{n} perso(s) disponible(s)")

    def context(self) -> str:
        lines = ["Personnages disponibles du joueur :"]
        for c in self.rv.owned_chars():
            b = self.buttons.get(c["id"])
            if b is not None and not b.isChecked():
                continue
            st = self.rv.roster.get(c["id"])
            extra = f", arme : {st['weapon']}" if st["weapon"] else ""
            lines.append(f"- {c['name']} ({c['rank']}★, {c['element']}, {c['weapon']}, C{st['cons']}, "
                         f"{PHASE_LABELS[st['phase']]}{extra})")
        return "\n".join(lines)

    def propose(self):
        if not any(b.isChecked() for b in self.buttons.values()):
            self.count.setText("⚠ Coche au moins quelques persos.")
            return
        q = f"Propose-moi les meilleures équipes pour : {self.goal.currentText()}."
        if self.main.currentIndex() > 0:
            q += f" Construis-les autour de {self.main.currentText()}."
        q += (" Utilise UNIQUEMENT mes persos disponibles. Pour chaque équipe : les 4 persos, leur rôle, "
              "la rotation en une ligne, et une alternative si un perso manque.")
        self.ask.emit(q, self.context())
