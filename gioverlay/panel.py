"""Panneau de contrôle : état, catégories affichées, actions."""
from __future__ import annotations

import sys

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from . import macos

STYLE = """
QWidget { background: #16181d; color: #e8e6e3; font-size: 13px; }
QPushButton#title { font-size: 17px; font-weight: 600; background: transparent; border: none;
                    text-align: left; padding: 2px 0; }
QPushButton#title:hover { color: #f5c542; }
QLabel#status { background: #1f2229; border-radius: 8px; padding: 8px; font-family: Menlo, Consolas, monospace; font-size: 12px; }
QLabel#hint { color: #8b8f98; font-size: 11px; }
QLabel#notes { background: #1f2229; border: 1px solid #2c303a; border-radius: 8px; padding: 7px 9px; font-size: 12px; }
QPushButton { background: #2a2e37; border: 1px solid #383d48; border-radius: 7px; padding: 7px 10px; }
QPushButton:hover { background: #333845; }
QPushButton#primary { background: #3b5bdb; border-color: #4c6ef5; }
QPushButton#ai { background: #6b4bc8; border-color: #8a6cf0; font-weight: 600; }
QPushButton#close:enabled { background: #4a2a30; border-color: #a34b57; }
QPushButton:disabled { color: #5d626d; }
QLineEdit { background: #1f2229; border: 1px solid #383d48; border-radius: 7px; padding: 6px 8px; }
QLabel#section { color: #b9a6ff; font-weight: 600; margin-top: 6px; }
QPushButton#sect { background: transparent; border: none; border-top: 1px solid #2a2e37; border-radius: 0;
                   text-align: left; padding: 7px 2px 4px 2px; color: #c9c6c1; font-weight: 600; }
QPushButton#sect:hover { color: #f5c542; }
QPushButton#skip { text-align: left; padding-left: 12px; }
QPushButton#update { background: #f5c542; color: #16181d; border: none; font-weight: 700; }
QPushButton#skip:checked { background: #14532d; border-color: #22c55e; color: #4ade80; font-weight: 700; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #5a606c; border-radius: 4px; background: #1f2229; }
QCheckBox::indicator:checked { background: #4c6ef5; border-color: #748ffc; }
"""


def _pretty(spec: str) -> str:
    parts = spec.lower().split("+")
    if sys.platform == "darwin":
        sym = {"ctrl": "⌃", "alt": "⌥", "cmd": "⌘", "shift": "⇧"}
        return "".join(sym.get(p, p.upper()) for p in parts)
    names = {"ctrl": "Ctrl", "alt": "Alt", "cmd": "Win", "shift": "Maj"}
    return "+".join(names.get(p, p.upper()) for p in parts)


class Panel(QWidget):
    calibrate = pyqtSignal()
    mark_nearest = pyqtSignal()
    undo_mark = pyqtSignal()
    toggle_overlay = pyqtSignal()
    relocalize = pyqtSignal()
    reset_progress = pyqtSignal()
    mark_view = pyqtSignal()
    groups_changed = pyqtSignal()
    quest_help = pyqtSignal()
    open_map = pyqtSignal()
    quest_close = pyqtSignal()
    api_key_changed = pyqtSignal(str)
    skip_toggled = pyqtSignal(bool)
    restart_update = pyqtSignal()
    dimension_chosen = pyqtSignal(int)

    def __init__(self, cfg: dict, counts: dict[str, tuple[int, int]]):
        super().__init__(None, Qt.WindowType.WindowStaysOnTopHint)
        self.cfg = cfg
        self.setWindowTitle("Genshin Overlay")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(330)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        opened = cfg.setdefault("panel_open", {"groups": False, "tools": False, "ai": False})
        if not cfg.get("minimap_region"):
            opened["tools"] = True   # pas encore calibré : le bouton « Calibrer » doit être visible

        # Le titre ouvre la carte interactive (fenêtre séparée).
        t = QPushButton("Genshin Overlay   🗺")
        t.setObjectName("title")
        t.setCursor(Qt.CursorShape.PointingHandCursor)
        t.setToolTip("Ouvrir la carte interactive (Carte, Builds, News, Paramètres)")
        t.clicked.connect(self.open_map)
        lay.addWidget(t)
        self.status = QLabel("Démarrage…")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.status)
        # Mise à jour prête (cachée tant qu'il n'y en a pas)
        self.b_update = QPushButton()
        self.b_update.setObjectName("update")
        self.b_update.clicked.connect(self.restart_update)
        self.b_update.hide()
        lay.addWidget(self.b_update)
        # Notes en temps réel HoYoLAB (résine, commissions…), si le compte est relié
        self.notes = QLabel()
        self.notes.setObjectName("notes")
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.TextFormat.RichText)
        self.notes.hide()
        lay.addWidget(self.notes)

        # --- dimension (Teyvat, Enkanomiya, le Gouffre…) ------------------------------------
        from . import dimensions
        row = QHBoxLayout()
        lab = QLabel("Dimension")
        lab.setObjectName("hint")
        self.dim = QComboBox()
        for mid, nm in dimensions.DIMENSIONS:
            self.dim.addItem(nm if dimensions.is_ready(mid) else f"{nm}  ⬇", mid)
        self.dim.setToolTip("Carte utilisée pour la mini-map et la carte du jeu.\n"
                            "⬇ = téléchargée depuis HoYoLAB au premier choix (moins d'une minute).\n"
                            "L'app change toute seule de dimension quand elle te retrouve ailleurs.")
        self.set_dimension(int(cfg.get("map_id", 2)))
        self.dim.activated.connect(lambda i: self.dimension_chosen.emit(int(self.dim.itemData(i))))
        row.addWidget(lab)
        row.addWidget(self.dim, 1)
        lay.addLayout(row)

        # --- actions principales (toujours visibles) ---------------------------------------
        hk = cfg["hotkeys"]
        self.hk_buttons: dict[str, tuple[QPushButton, str]] = {}
        row = QHBoxLayout()
        b_mark = QPushButton(f"Marquer fait  {_pretty(hk['mark_nearest'])}")
        b_mark.clicked.connect(self.mark_nearest)
        b_undo = QPushButton(f"Annuler  {_pretty(hk['undo_mark'])}")
        b_undo.clicked.connect(self.undo_mark)
        row.addWidget(b_mark)
        row.addWidget(b_undo)
        lay.addLayout(row)
        row = QHBoxLayout()
        b_hide = QPushButton(f"Masquer  {_pretty(hk['toggle_overlay'])}")
        b_hide.clicked.connect(self.toggle_overlay)
        b_reloc = QPushButton(f"Relocaliser  {_pretty(hk['relocalize'])}")
        b_reloc.clicked.connect(self.relocalize)
        row.addWidget(b_hide)
        row.addWidget(b_reloc)
        lay.addLayout(row)
        self.b_skip = QPushButton()
        self.b_skip.setObjectName("skip")
        self.b_skip.setCheckable(True)
        self.b_skip.setToolTip("Pendant un dialogue (boutons Auto / liste / masquer / son visibles en haut à gauche),\n"
                               "appuie sur F à ta place. « Dialogue terminé » s'affiche quand Paimon et la mini-map reviennent.\n"
                               "Sur un choix de réponse, F choisit l'option surlignée.")
        self.b_skip.toggled.connect(self._skip_toggled)
        self.b_skip.setChecked(bool(cfg.get("auto_skip", False)))
        self._skip_toggled(self.b_skip.isChecked(), emit=False)
        lay.addWidget(self.b_skip)

        # --- section repliable : ce qui s'affiche sur la mini-map ------------------------------
        self.sections: dict[str, tuple[QPushButton, QWidget, str]] = {}
        body = self._section(lay, "groups", "🗺  Affichage sur la mini-map", opened)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setContentsMargins(0, 0, 0, 0)
        self.checks: dict[str, QCheckBox] = {}
        self.count_labels: dict[str, QLabel] = {}
        for r, (name, g) in enumerate(cfg["groups"].items()):
            sw = QFrame()
            sw.setFixedSize(12, 12)
            sw.setStyleSheet(f"background:{g['color']}; border-radius:6px;")
            cb = QCheckBox(name)
            cb.setChecked(g.get("enabled", True))
            cb.toggled.connect(lambda on, n=name: self._toggle_group(n, on))
            cnt = QLabel()
            cnt.setObjectName("hint")
            grid.addWidget(sw, r, 0)
            grid.addWidget(cb, r, 1)
            grid.addWidget(cnt, r, 2, Qt.AlignmentFlag.AlignRight)
            self.checks[name] = cb
            self.count_labels[name] = cnt
        body.addLayout(grid)
        self.update_counts(counts)
        self.layered = QCheckBox("Points d'étages / souterrains (pointillés)")
        self.layered.setChecked(cfg.get("show_layered_points", True))
        self.layered.toggled.connect(self._toggle_layered)
        body.addWidget(self.layered)
        self.set_section("groups", bool(opened.get("groups")), shrink=False)   # affiche le compteur (n/7)

        # --- section repliable : outils ----------------------------------------------------
        body = self._section(lay, "tools", "🔧  Outils", opened)
        b_cal = QPushButton("Calibrer la mini-map")
        b_cal.setObjectName("primary")
        b_cal.clicked.connect(self.calibrate)
        body.addWidget(b_cal)
        b_view = QPushButton(f"Tout marquer fait sur la carte affichée  {_pretty(hk.get('mark_view', 'ctrl+alt+v'))}")
        b_view.setToolTip("Ouvre la carte du jeu sur une zone déjà explorée à 100 %, puis clique ici : "
                          "tous les points visibles disparaissent.")
        b_view.clicked.connect(self.mark_view)
        body.addWidget(b_view)
        b_reset = QPushButton("Réinitialiser la progression…")
        b_reset.clicked.connect(self._confirm_reset)
        body.addWidget(b_reset)
        self.hk_buttons.update({"mark_nearest": (b_mark, "Marquer fait"), "undo_mark": (b_undo, "Annuler"),
                                "toggle_overlay": (b_hide, "Masquer"), "relocalize": (b_reloc, "Relocaliser"),
                                "mark_view": (b_view, "Tout marquer fait sur la carte affichée")})

        # --- section repliable : aide de quête par IA -------------------------------------------
        gem = cfg.get("quest_provider", "gemini") == "gemini"
        self.b_close_quest = QPushButton("✕ Fermer l'aide")
        self.b_close_quest.setObjectName("close")
        self.b_close_quest.setToolTip("Fermer la carte d'aide affichée à l'écran")
        self.b_close_quest.clicked.connect(self.quest_close)
        self.b_close_quest.hide()
        body = self._section(lay, "ai", "✨  Mode IA" + (" (Gemini)" if gem else " (Claude)"), opened,
                             extra=self.b_close_quest)
        self.question = QLineEdit()
        self.question.setPlaceholderText("Facultatif : où bloques-tu ? (ex. « l'énigme des braseros »)")
        self.question.returnPressed.connect(self.quest_help)
        body.addWidget(self.question)
        b_ai = QPushButton(f"Aide sur ma quête  {_pretty(hk.get('quest_help', 'ctrl+alt+q'))}")
        b_ai.setObjectName("ai")
        b_ai.clicked.connect(self.quest_help)
        body.addWidget(b_ai)
        self.hk_buttons["quest_help"] = (b_ai, "Aide sur ma quête")
        b_key = QPushButton("Clé API Gemini…" if gem else "Clé API Claude…")
        b_key.clicked.connect(self.ask_api_key)
        body.addWidget(b_key)

        self.hint = QLabel("« Marquer fait » coche le point le plus proche du joueur (coffre ouvert, défi fini).")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

    # ------------------------------------------------------------------ sections repliables
    def _section(self, lay, key: str, title: str, opened: dict, extra=None) -> QVBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        head = QPushButton()
        head.setObjectName("sect")
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        head.setToolTip("Cliquer pour afficher / masquer")
        row.addWidget(head, 1)
        if extra is not None:
            row.addWidget(extra)
        lay.addLayout(row)
        body = QWidget()
        body.setObjectName("sectbody")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(4, 0, 0, 4)
        bl.setSpacing(6)
        lay.addWidget(body)
        self.sections[key] = (head, body, title)
        head.clicked.connect(lambda: self.set_section(key, not body.isVisible()))
        self.set_section(key, bool(opened.get(key)), shrink=False)
        return bl

    def set_section(self, key: str, show: bool, shrink=True):
        head, body, title = self.sections[key]
        body.setVisible(show)
        extra = ""
        if key == "groups" and hasattr(self, "checks"):
            n = sum(1 for cb in self.checks.values() if cb.isChecked())
            extra = f"   ({n}/{len(self.checks)})"
        head.setText(f"{'▾' if show else '▸'}  {title}{extra}")
        self.cfg.setdefault("panel_open", {})[key] = show
        if shrink:
            QTimer.singleShot(0, self._shrink)

    def _shrink(self):
        """Ajuste la hauteur du panneau à son contenu (plus compact quand on replie)."""
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    def showEvent(self, e):
        super().showEvent(e)
        macos.make_floating_window(self)

    def _toggle_group(self, name, on):
        self.cfg["groups"][name]["enabled"] = on
        self.set_section("groups", True, shrink=False)
        self.groups_changed.emit()

    def _toggle_layered(self, on):
        self.cfg["show_layered_points"] = on
        self.groups_changed.emit()

    def _skip_toggled(self, on: bool, emit=True):
        self.b_skip.setText("⏩  Passer les dialogues : ON" if on else "⏩  Passer les dialogues : OFF")
        self.cfg["auto_skip"] = on
        if emit:
            self.skip_toggled.emit(on)

    def show_update(self, version: str):
        self.b_update.setText(f"🔄  Mise à jour {version} prête — Redémarrer")
        self.b_update.show()
        QTimer.singleShot(0, self._shrink)

    def set_hotkeys(self, hk: dict):
        """Met à jour les raccourcis affichés sur les boutons (après un changement dans les Paramètres)."""
        for name, (b, label) in self.hk_buttons.items():
            b.setText(f"{label}  {_pretty(hk.get(name, ''))}" if hk.get(name) else label)

    def set_notes(self, html: str | None):
        if html:
            self.notes.setText(html)
            self.notes.show()
        else:
            self.notes.hide()

    def set_quest_open(self, is_open: bool):
        self.b_close_quest.setVisible(is_open)
        self.b_close_quest.setEnabled(is_open)

    def ask_api_key(self):
        if self.cfg.get("quest_provider", "gemini") == "gemini":
            title, field = "Clé API Gemini (gratuite)", "gemini_api_key"
            text = ("Colle ta clé API Gemini (elle commence par « AQ. » ou « AIza »).\n\n"
                    "Pour l'obtenir gratuitement : aistudio.google.com → connecte-toi avec\n"
                    "un compte Google → « Get API key » → « Create API key ».\n"
                    "Aucune carte bancaire n'est nécessaire.")
        else:
            title, field = "Clé API Claude", "anthropic_api_key"
            text = ("Colle ta clé API (elle commence par « sk-ant- »).\n"
                    "Elle se crée sur console.anthropic.com → API Keys.\n"
                    "Un abonnement claude.ai ne suffit pas : l'API se paie à part, à l'usage.")
        key, ok = QInputDialog.getText(self, title, text, QLineEdit.EchoMode.Password, self.cfg.get(field, ""))
        if ok:
            self.api_key_changed.emit(key.strip())

    def _confirm_reset(self):
        r = QMessageBox.question(self, "Réinitialiser",
                                 "Effacer tous les points marqués comme faits ?")
        if r == QMessageBox.StandardButton.Yes:
            self.reset_progress.emit()

    def update_counts(self, counts: dict[str, tuple[int, int]]):
        for name, (done, total) in counts.items():
            if name in self.count_labels:
                self.count_labels[name].setText(f"{done}/{total}")

    def set_dimension(self, map_id: int, ready_ids=None):
        from . import dimensions
        self.dim.blockSignals(True)
        for i in range(self.dim.count()):
            mid = int(self.dim.itemData(i))
            nm = dimensions.name(mid)
            self.dim.setItemText(i, nm if dimensions.is_ready(mid) else f"{nm}  ⬇")
            if mid == map_id:
                self.dim.setCurrentIndex(i)
        self.dim.blockSignals(False)

    def set_status(self, html: str):
        self.status.setText(html)

    def flash(self, text: str):
        self.hint.setText(text)
