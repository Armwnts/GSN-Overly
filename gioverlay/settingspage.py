"""Page « Paramètres » : couleur d'accentuation, raccourcis clavier, compte Genshin (UID, import
de la vitrine), HoYoLAB (notes en temps réel, connexion quotidienne) et clé Gemini."""
from __future__ import annotations

import sys
import time

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from . import theme
from .uikit import COMMON_STYLE, Job, JobOwner

STYLE = COMMON_STYLE + """
QWidget#settings { background: #111318; }
QWidget#settings QLabel { color: #e8e6e3; }
QFrame#card { background: #16181d; border: 1px solid #262a33; border-radius: 12px; }
QLabel#ctitle { color: #f5c542; font-size: 16px; font-weight: 700; }
QLabel#ok { color: #74c2a8; }
QLabel#err { color: #e0a84a; }
QLineEdit, QSpinBox { background: #1f2229; color: #e8e6e3; border: 1px solid #383d48; border-radius: 7px; padding: 6px 8px; }
QPushButton { background: #2a2e37; color: #e8e6e3; border: 1px solid #383d48; border-radius: 7px; padding: 6px 12px; }
QPushButton:hover { background: #333845; }
QPushButton#go { background: #f5c542; color: #16181d; border: none; font-weight: 700; }
QCheckBox { color: #e8e6e3; }
"""

ACTIONS = [("toggle_overlay", "Afficher / masquer l'overlay"), ("mark_nearest", "Marquer fait (le plus proche)"),
           ("undo_mark", "Annuler le dernier marquage"), ("relocalize", "Relocaliser"),
           ("mark_view", "Tout marquer fait sur la carte affichée"), ("quest_help", "Aide sur ma quête (Gemini)")]
DEFAULT_HK = {"toggle_overlay": "ctrl+alt+o", "mark_nearest": "ctrl+alt+m", "undo_mark": "ctrl+alt+z",
              "relocalize": "ctrl+alt+r", "mark_view": "ctrl+alt+v", "quest_help": "ctrl+alt+q"}
MAC = sys.platform == "darwin"
MODS = [("ctrl", "⌃ Contrôle" if MAC else "Ctrl"), ("alt", "⌥ Option" if MAC else "Alt"),
        ("cmd", "⌘ Commande" if MAC else "Win"), ("shift", "⇧ Maj")]

COOKIE_HELP = """<b>Trouver tes cookies HoYoLAB</b> (une seule fois, ~1 min) :<br>
1. Dans Chrome / Safari, va sur <b>www.hoyolab.com</b> et connecte-toi.<br>
2. Ouvre les outils de développement : <b>⌥⌘I</b> (Chrome) — sur Safari, active d'abord
« Afficher les fonctionnalités pour les développeurs web » dans Réglages → Avancés.<br>
3. Onglet <b>Application</b> (Chrome) ou <b>Stockage</b> (Safari) → <b>Cookies</b> → hoyolab.com.<br>
4. Copie la valeur de <b>ltuid_v2</b> et de <b>ltoken_v2</b>, puis colle-les ci-dessous sous la forme
<code>ltuid_v2=…; ltoken_v2=…</code><br>
<span style='color:#e0a84a'>⚠ Ces cookies donnent accès à ton compte HoYoLAB : ne les partage jamais.
Ils restent uniquement sur cet ordinateur.</span><br>
Pense aussi à rendre tes notes visibles : HoYoLAB → ton profil → Battle Chronicle → ⚙ → « Notes en temps réel »."""


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(18, 14, 18, 16)
    v.setSpacing(10)
    t = QLabel(title)
    t.setObjectName("ctitle")
    v.addWidget(t)
    return f, v


def _muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


class SettingsPage(QWidget, JobOwner):
    accent_changed = pyqtSignal(str)
    roster_imported = pyqtSignal()

    def __init__(self, cfg: dict, save_cfg, shared, ctrl=None):
        super().__init__()
        self.setObjectName("settings")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.cfg, self.save_cfg, self.shared, self.ctrl = cfg, save_cfg, shared, ctrl

        inner = QWidget()
        inner.setObjectName("settings")
        col = QVBoxLayout(inner)
        col.setContentsMargins(28, 20, 28, 28)
        col.setSpacing(16)
        title = QLabel("Paramètres")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #f5c542;")
        col.addWidget(title)
        cols = QHBoxLayout()
        cols.setSpacing(16)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(16)
        right.setSpacing(16)
        for wdg in (self._appearance(), self._updates(), self._account(), self._gemini(), self._diagnostic()):
            left.addWidget(wdg)
        left.addStretch(1)
        for wdg in (self._hoyolab(), self._hotkeys()):
            right.addWidget(wdg)
        right.addStretch(1)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        col.addLayout(cols)
        col.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(inner)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(sc)

        self._tick = QTimer(self, interval=1000, timeout=self._refresh_live)
        self._tick.start()
        if ctrl is not None and hasattr(ctrl, "notes_updated"):
            ctrl.notes_updated.connect(self._notes_updated)
            ctrl.checkin_done.connect(self._checkin_done)

    # ================================================================== apparence
    def _appearance(self):
        f, v = _card("🎨 Apparence")
        v.addWidget(_muted("Couleur d'accentuation de l'application (titres, onglets, boutons principaux)."))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.swatches = []
        for name, color in theme.PRESETS:
            b = QPushButton()
            b.setToolTip(name)
            b.setFixedSize(34, 34)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=color: self.set_accent(c))
            b.setProperty("color", color)
            row.addWidget(b)
            self.swatches.append(b)
        row.addStretch(1)
        v.addLayout(row)
        row2 = QHBoxLayout()
        custom = QPushButton("Couleur personnalisée…")
        custom.clicked.connect(self._pick_color)
        row2.addWidget(custom)
        self.accent_lab = QLabel()
        row2.addWidget(self.accent_lab)
        row2.addStretch(1)
        v.addLayout(row2)
        self._paint_swatches()
        return f

    def _paint_swatches(self):
        cur = theme.accent()
        for b in self.swatches:
            c = b.property("color")
            border = "3px solid #ffffff" if c.upper() == cur else "1px solid #383d48"
            b.setStyleSheet(f"QPushButton {{ background: {c}; border: {border}; border-radius: 17px; }}")
        self.accent_lab.setText(f"Actuelle : <b style='color:{cur}'>{cur}</b>")

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(theme.accent()), self, "Couleur d'accentuation")
        if c.isValid():
            self.set_accent(c.name())

    def set_accent(self, color: str):
        new = theme.set_accent(color)
        self.cfg["accent_color"] = new
        self.save_cfg()
        self._paint_swatches()
        self.accent_changed.emit(new)

    # ================================================================== mises à jour
    def _updates(self):
        from .updater import current_version
        f, v = _card("🔄 Mises à jour")
        v.addWidget(QLabel(f"Version installée : <b>{current_version()}</b>"))
        self.upd_status = QLabel(getattr(self.ctrl, "update_text", "") if self.ctrl else "")
        self.upd_status.setWordWrap(True)
        v.addWidget(self.upd_status)
        row = QHBoxLayout()
        b = QPushButton("Vérifier maintenant")
        b.clicked.connect(lambda: self.ctrl and self.ctrl.check_update(manual=True))
        self.b_restart = QPushButton("Redémarrer pour mettre à jour")
        self.b_restart.setObjectName("go")
        self.b_restart.clicked.connect(lambda: self.ctrl and self.ctrl.restart_for_update())
        self.b_restart.setVisible(bool(self.ctrl and getattr(self.ctrl, "update_ready", None)))
        row.addWidget(b)
        row.addWidget(self.b_restart)
        row.addStretch(1)
        v.addLayout(row)
        self.upd_auto = QCheckBox("Installer automatiquement les mises à jour (vérification toutes les 6 h)")
        self.upd_auto.setChecked(self.cfg.get("auto_update", True))
        self.upd_auto.toggled.connect(lambda on: (self.cfg.__setitem__("auto_update", on), self.save_cfg()))
        v.addWidget(self.upd_auto)
        if self.ctrl is not None and hasattr(self.ctrl, "update_status"):
            self.ctrl.update_status.connect(self._upd_changed)
        return f

    def _upd_changed(self, text):
        self.upd_status.setText(text)
        self.b_restart.setVisible(bool(self.ctrl and getattr(self.ctrl, "update_ready", None)))

    def _dock_toggled(self, on):
        import os
        from . import macos
        self.cfg["dock_icon"] = on
        self.save_cfg()
        macos.show_dock_icon(on, os.environ.get("GIO_APP_ICON"))

    # ================================================================== diagnostic
    def _diagnostic(self):
        from . import diag
        f, v = _card("🩺 Diagnostic")
        v.addWidget(_muted("L'app note ses erreurs dans un journal et prend une capture de l'écran du jeu quand "
                           "quelque chose se passe mal (position introuvable, dialogue douteux…). Le rapport "
                           "rassemble tout pour Claude — sans ta clé Gemini ni tes cookies HoYoLAB. Les erreurs ne sont "
                           "jamais effacées : elles sont gardées (avec ce qui s'est passé juste avant) jusqu'au "
                           "prochain rapport, et une notification te le rappelle."))
        self.diag_counts = QLabel()
        v.addWidget(self.diag_counts)
        self.diag_caps = QCheckBox("Captures d'écran automatiques en cas de problème")
        self.diag_caps.setChecked(bool(self.cfg.get("diag_captures", True)))
        self.diag_caps.toggled.connect(self._diag_caps_toggled)
        v.addWidget(self.diag_caps)
        row = QHBoxLayout()
        b_rep = QPushButton("📦 Créer un rapport pour Claude")
        b_rep.setObjectName("go")
        b_rep.clicked.connect(self._make_report)
        b_dir = QPushButton("Ouvrir le dossier des journaux")
        b_dir.clicked.connect(lambda: diag.logs_dir() and QDesktopServices.openUrl(QUrl.fromLocalFile(str(diag.logs_dir()))))
        row.addWidget(b_rep)
        row.addWidget(b_dir)
        row.addStretch(1)
        v.addLayout(row)
        self.diag_status = QLabel()
        self.diag_status.setWordWrap(True)
        self.diag_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.diag_status)
        return f

    def _diag_caps_toggled(self, on):
        from . import diag
        diag.captures_enabled = on
        self.cfg["diag_captures"] = on
        self.save_cfg()

    def _make_report(self):
        from . import diag
        try:
            extra = {}
            if self.ctrl is not None:
                hk = getattr(self.ctrl, "hotkeys", None)
                extra["raccourcis"] = hk.status() if hk else "désactivés"
                extra["mini-map calibrée"] = bool(self.cfg.get("minimap_region"))
            path = diag.make_report(self.cfg, extra)
        except Exception as e:  # noqa: BLE001
            self.diag_status.setText(f"<span style='color:#e0a84a'>⚠ Rapport impossible : {e}</span>")
            return
        self.diag_status.setText(f"✓ Rapport créé : <b>{path.name}</b><br>dans le dossier <b>{path.parent}</b>."
                                 "<br>Dis-le à Claude (il peut le lire si ton Mac est relié), ou envoie-lui le fichier.")
        if sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", "-R", str(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # ================================================================== Gemini
    def _gemini(self):
        f, v = _card("✨ Gemini (IA gratuite)")
        v.addWidget(_muted("Clé utilisée par l'aide de quête et les Builds. Gratuite sur aistudio.google.com "
                           "→ « Get API key »."))
        row = QHBoxLayout()
        self.gem_key = QLineEdit(self.cfg.get("gemini_api_key", ""))
        self.gem_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.gem_key.setPlaceholderText("Colle ta clé API Gemini")
        b = QPushButton("Enregistrer")
        b.clicked.connect(self._save_gem)
        row.addWidget(self.gem_key, 1)
        row.addWidget(b)
        v.addLayout(row)
        self.gem_status = _muted("")
        v.addWidget(self.gem_status)
        return f

    def _save_gem(self):
        self.cfg["gemini_api_key"] = self.gem_key.text().strip()
        self.save_cfg()
        self.gem_status.setText("✓ Clé enregistrée." if self.cfg["gemini_api_key"] else "Clé effacée.")

    # ================================================================== raccourcis
    def _hotkeys(self):
        f, v = _card("⌨ Raccourcis clavier")
        self.hk_status = QLabel()
        self.hk_status.setWordWrap(True)
        v.addWidget(self.hk_status)
        g = QGridLayout()
        g.setHorizontalSpacing(6)
        self.hk_rows = {}
        for r, (name, label) in enumerate(ACTIONS):
            g.addWidget(QLabel(label), r * 2, 0, 1, 6)
            boxes = {}
            for c, (m, ml) in enumerate(MODS):
                cb = QCheckBox(ml)
                boxes[m] = cb
                g.addWidget(cb, r * 2 + 1, c)
            key = QLineEdit()
            key.setMaxLength(1)
            key.setFixedWidth(46)
            key.setAlignment(Qt.AlignmentFlag.AlignCenter)
            key.setPlaceholderText("?")
            g.addWidget(key, r * 2 + 1, 4)
            self.hk_rows[name] = (boxes, key)
        g.setColumnStretch(5, 1)
        v.addLayout(g)
        row = QHBoxLayout()
        b_save = QPushButton("Enregistrer les raccourcis")
        b_save.setObjectName("go")
        b_save.clicked.connect(self._save_hotkeys)
        b_def = QPushButton("Par défaut")
        b_def.clicked.connect(lambda: self._load_hotkeys(DEFAULT_HK))
        row.addWidget(b_save)
        row.addWidget(b_def)
        row.addStretch(1)
        v.addLayout(row)
        self.hk_last = QLabel("Test : appuie sur un raccourci, il s'affichera ici.")
        self.hk_last.setObjectName("muted")
        v.addWidget(self.hk_last)
        self.hk_enabled = QCheckBox("Activer les raccourcis clavier")
        self.hk_enabled.setChecked(self.cfg.get("hotkeys_enabled", True))
        v.addWidget(self.hk_enabled)
        if MAC:
            b_ax = QPushButton("Ouvrir les réglages « Accessibilité » (si un raccourci ne marche pas)")
            b_ax.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")))
            v.addWidget(b_ax)
        v.addWidget(_muted("Conseil : garde au moins ⌃ + ⌥ (ou ⌘) pour ne pas gêner le jeu. "
                           "Une seule lettre ou un chiffre par raccourci."))
        self._load_hotkeys(self.cfg.get("hotkeys", DEFAULT_HK))
        return f

    def _load_hotkeys(self, hk: dict):
        from .macos import parse_hotkey
        for name, (boxes, key) in self.hk_rows.items():
            try:
                mods, k = parse_hotkey(hk.get(name, DEFAULT_HK[name]))
            except ValueError:
                mods, k = frozenset(), ""
            for m, cb in boxes.items():
                cb.setChecked(m in mods)
            key.setText(k.upper())

    def _save_hotkeys(self):
        hk, problems = {}, []
        for name, (boxes, key) in self.hk_rows.items():
            k = key.text().strip().lower()
            mods = [m for m, _ in MODS if boxes[m].isChecked()]
            if not k:
                problems.append(f"« {dict(ACTIONS)[name]} » : pas de touche")
                continue
            if not mods:
                problems.append(f"« {dict(ACTIONS)[name]} » : ajoute au moins une touche ⌃/⌥/⌘")
                continue
            hk[name] = "+".join(mods + [k])
        dup = {v for v in hk.values() if list(hk.values()).count(v) > 1}
        if dup:
            problems.append("même raccourci utilisé deux fois")
        if problems:
            self._hk_problem = "⚠ " + " ; ".join(problems)
            self.hk_status.setText(f"<span style='color:#e0a84a'>{self._hk_problem}</span>")
            return
        self._hk_problem = ""
        self.cfg["hotkeys"] = hk
        self.cfg["hotkeys_enabled"] = self.hk_enabled.isChecked()
        self.save_cfg()
        if self.ctrl is not None and hasattr(self.ctrl, "restart_hotkeys"):
            self.ctrl.restart_hotkeys()
        self._refresh_live()

    # ================================================================== compte / UID
    def _account(self):
        f, v = _card("👤 Mon compte Genshin")
        v.addWidget(_muted("Ton UID est en bas à droite de l'écran du jeu. L'import récupère les persos de ta "
                           "vitrine (profil en jeu) : niveau, aptitudes, constellations, arme, artéfacts et stats."))
        row = QHBoxLayout()
        self.uid = QLineEdit(self.cfg.get("uid", ""))
        self.uid.setPlaceholderText("UID (ex. 712345678)")
        self.uid.setMaxLength(10)
        self.uid.editingFinished.connect(self._save_uid)
        b = QPushButton("⬇ Importer mes persos")
        b.setObjectName("go")
        b.clicked.connect(self.import_showcase)
        row.addWidget(self.uid, 1)
        row.addWidget(b)
        v.addLayout(row)
        self.imp_status = QLabel()
        self.imp_status.setWordWrap(True)
        v.addWidget(self.imp_status)
        v.addWidget(_muted("Dans le jeu : Menu → Profil → Modifier la vitrine (jusqu'à 12 persos) et active "
                           "« Afficher les détails des personnages ». Données : Enka.Network."))
        return f

    def _save_uid(self):
        self.cfg["uid"] = "".join(ch for ch in self.uid.text() if ch.isdigit())
        self.save_cfg()

    def import_showcase(self):
        self._save_uid()
        uid = self.cfg["uid"]
        if not uid:
            self.imp_status.setText("⚠ Entre d'abord ton UID.")
            return
        gd, roster = self.shared()
        from .enka import EnkaClient, apply_to_roster
        client = EnkaClient(gd.dir.parent / "enka")
        self.imp_status.setText("Import en cours… (la première fois, téléchargement des noms d'objets : ~30 s)")

        def run():
            info, chars = client.import_showcase(uid, gd)
            return info, chars

        def done(res):
            info, chars = res
            n = apply_to_roster(roster, chars)
            who = f"{info['nickname']} (rang d'aventure {info['level']})" if info.get("nickname") else "Joueur"
            if not n:
                self.imp_status.setText(f"⚠ {who} : aucun perso détaillé dans la vitrine. Active « Afficher les "
                                        "détails des personnages » dans ton profil en jeu, attends ~1 min puis réessaie.")
            else:
                names = ", ".join(self._names(gd, [c["id"] for c in chars]))
                self.imp_status.setText(f"✓ {who} : {n} perso(s) importé(s) dans « Mes persos » : {names}.")
            self.roster_imported.emit()
        self._run(Job(run), done, lambda m: self.imp_status.setText(f"⚠ {m}"))

    @staticmethod
    def _names(gd, ids):
        try:
            by = {c["id"]: c["name"] for c in gd.characters()}
        except Exception:  # noqa: BLE001
            by = {}
        return [by.get(i, i) for i in ids]

    # ================================================================== HoYoLAB
    def _hoyolab(self):
        f, v = _card("📒 HoYoLAB : notes en temps réel")
        v.addWidget(_muted("Affiche dans le panneau ta résine, tes commissions, expéditions et pièces de royaume, "
                           "te prévient quand la résine est pleine, et peut récupérer la récompense de connexion "
                           "quotidienne HoYoLAB."))
        help_lab = QLabel(COOKIE_HELP)
        help_lab.setWordWrap(True)
        help_lab.setTextFormat(Qt.TextFormat.RichText)
        help_lab.setStyleSheet("background: #1b1e24; border-radius: 8px; padding: 10px; font-size: 12px;")
        help_lab.setVisible(not self.cfg.get("hoyolab_cookies"))
        b_help = QPushButton("Comment trouver mes cookies ?")
        b_help.clicked.connect(lambda: help_lab.setVisible(not help_lab.isVisible()))
        v.addWidget(b_help)
        v.addWidget(help_lab)
        row = QHBoxLayout()
        self.cookies = QLineEdit()
        self.cookies.setEchoMode(QLineEdit.EchoMode.Password)
        n = len(self.cfg.get("hoyolab_cookies") or {})
        self.cookies.setPlaceholderText(f"Cookies enregistrés ({n}) — colle-en de nouveaux pour les remplacer"
                                        if n else "ltuid_v2=…; ltoken_v2=…")
        b = QPushButton("Enregistrer et tester")
        b.setObjectName("go")
        b.clicked.connect(self._save_cookies)
        row.addWidget(self.cookies, 1)
        row.addWidget(b)
        v.addLayout(row)
        self.notes_status = QLabel()
        self.notes_status.setWordWrap(True)
        self.notes_status.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.notes_status)
        g = QGridLayout()
        self.n_show = QCheckBox("Afficher les notes dans le panneau")
        self.n_show.setChecked(self.cfg.get("notes_enabled", True))
        self.n_alert = QCheckBox("Me prévenir quand la résine atteint")
        self.n_alert.setChecked(self.cfg.get("resin_alert_on", True))
        self.n_thr = QSpinBox()
        self.n_thr.setRange(20, 200)
        self.n_thr.setValue(int(self.cfg.get("resin_alert", 200)))
        self.n_auto = QCheckBox("Connexion quotidienne HoYoLAB automatique (récompense du jour)")
        self.n_auto.setChecked(self.cfg.get("auto_checkin", True))
        g.addWidget(self.n_show, 0, 0, 1, 2)
        g.addWidget(self.n_alert, 1, 0)
        g.addWidget(self.n_thr, 1, 1)
        g.addWidget(self.n_auto, 2, 0, 1, 2)
        self.n_events = QCheckBox("Me prévenir quand un événement se termine bientôt (moins de 36 h)")
        self.n_events.setChecked(self.cfg.get("events_alert", True))
        g.addWidget(self.n_events, 3, 0, 1, 2)
        self.n_bar = QCheckBox("Widget dans la barre des menus 🌙 (résine, argent du domaine, événements)"
                               if sys.platform == "darwin" else
                               "Icône dans la zone de notification (résine, argent du domaine, événements)")
        self.n_bar.setChecked(self.cfg.get("menubar_widget", True))
        self.n_bar.toggled.connect(self._menubar_toggled)
        g.addWidget(self.n_bar, 4, 0, 1, 2)
        self.n_desk = QCheckBox("Mini-widget sur le bureau (résine, argent du domaine, événements)")
        self.n_desk.setChecked(self.cfg.get("desk_widget", True))
        self.n_desk.toggled.connect(lambda on: self.ctrl and self.ctrl.set_desk_widget(on))
        g.addWidget(self.n_desk, 5, 0, 1, 2)
        self.n_desk_top = QCheckBox("… toujours au-dessus des fenêtres (sinon posé sur le bureau)")
        self.n_desk_top.setChecked(self.cfg.get("desk_widget_ontop", False))
        self.n_desk_top.toggled.connect(self._desk_top_toggled)
        g.addWidget(self.n_desk_top, 6, 0, 1, 2)
        v.addLayout(g)
        for w in (self.n_show, self.n_alert, self.n_auto, self.n_events):
            w.toggled.connect(self._save_notes_opts)
        self.n_thr.valueChanged.connect(self._save_notes_opts)
        row2 = QHBoxLayout()
        b_ref = QPushButton("⟳ Actualiser les notes")
        b_ref.clicked.connect(lambda: self.ctrl and self.ctrl.refresh_notes(force=True))
        b_ci = QPushButton("🎁 Récupérer la récompense du jour")
        b_ci.clicked.connect(lambda: self.ctrl and self.ctrl.do_checkin(manual=True))
        b_del = QPushButton("Oublier mes cookies")
        b_del.clicked.connect(self._forget_cookies)
        row2.addWidget(b_ref)
        row2.addWidget(b_ci)
        row2.addWidget(b_del)
        row2.addStretch(1)
        v.addLayout(row2)
        self.ci_status = _muted("")
        v.addWidget(self.ci_status)
        if self.ctrl is not None and getattr(self.ctrl, "notes", None):
            self._notes_updated(self.ctrl.notes)
        return f

    def _desk_top_toggled(self, on):
        self.cfg["desk_widget_ontop"] = on
        self.save_cfg()
        if self.ctrl is not None and getattr(self.ctrl, "desk", None) is not None:
            self.ctrl.set_desk_widget(False)
            self.ctrl.set_desk_widget(True)

    def _menubar_toggled(self, on):
        self.cfg["menubar_widget"] = on
        self.save_cfg()
        if self.ctrl is not None and hasattr(self.ctrl, "set_menubar"):
            self.ctrl.set_menubar(on)

    def _save_notes_opts(self, *_):
        self.cfg["notes_enabled"] = self.n_show.isChecked()
        self.cfg["resin_alert_on"] = self.n_alert.isChecked()
        self.cfg["resin_alert"] = self.n_thr.value()
        self.cfg["auto_checkin"] = self.n_auto.isChecked()
        self.cfg["events_alert"] = self.n_events.isChecked()
        self.save_cfg()
        if self.ctrl is not None and hasattr(self.ctrl, "render_notes"):
            self.ctrl.render_notes()

    def _save_cookies(self):
        from .hoyonotes import parse_cookies
        self._save_uid()
        text = self.cookies.text().strip()
        if text:
            ck = parse_cookies(text)
            if not ck.get("ltuid_v2") or not ck.get("ltoken_v2"):
                self.notes_status.setText("<span style='color:#e0a84a'>⚠ Je ne trouve pas ltuid_v2 et ltoken_v2 "
                                          "dans ce que tu as collé.</span>")
                return
            self.cfg["hoyolab_cookies"] = ck
            self.save_cfg()
            self.cookies.clear()
            self.cookies.setPlaceholderText(f"Cookies enregistrés ({len(ck)}) — colle-en de nouveaux pour les remplacer")
        if not self.cfg.get("uid"):
            self.notes_status.setText("<span style='color:#e0a84a'>⚠ Indique aussi ton UID (carte « Mon compte »).</span>")
            return
        self.notes_status.setText("Test en cours…")
        if self.ctrl is not None:
            self.ctrl.refresh_notes(force=True)

    def _forget_cookies(self):
        self.cfg["hoyolab_cookies"] = {}
        self.save_cfg()
        self.cookies.setPlaceholderText("ltuid_v2=…; ltoken_v2=…")
        self.notes_status.setText("Cookies effacés.")
        if self.ctrl is not None and hasattr(self.ctrl, "render_notes"):
            self.ctrl.notes = None
            self.ctrl.render_notes()

    def _notes_updated(self, notes):
        if isinstance(notes, str):
            self.notes_status.setText(f"<span style='color:#e0a84a'>⚠ {notes}</span>")
            return
        if not notes:
            return
        from .hoyonotes import duration, resin_now
        r = resin_now(notes)
        full = "pleine" if r >= notes["max_resin"] else f"pleine dans {duration(notes['resin_full_at'] - time.time())}"
        self.notes_status.setText(
            f"<span style='color:#74c2a8'>✓ Connecté</span> — résine <b>{r}</b>/{notes['max_resin']} ({full}), "
            f"commissions {notes['tasks_done']}/{notes['tasks_total']}, "
            f"expéditions {notes['exp_done']}/{notes['exp_total']} terminées.")

    def _checkin_done(self, msg: str):
        self.ci_status.setText(msg)

    # ================================================================== temps réel
    def _refresh_live(self):
        from . import diag
        c = diag.counts()
        pend = diag.pending_errors()
        keep = (f"<br><span style='color:#e0a84a'>⚠ <b>{pend}</b> erreur(s) gardée(s) en attente d'un rapport "
                f"(elles ne seront pas effacées).</span>" if pend else "<br>✓ Aucune erreur en attente.")
        self.diag_counts.setText(f"Depuis le lancement : <b>{c['errors']}</b> erreur(s), "
                                 f"<b>{c['warnings']}</b> avertissement(s).{keep}")
        if getattr(self, "_hk_problem", ""):
            return
        if self.ctrl is None:
            self.hk_status.setText("Raccourcis : état inconnu.")
            return
        hk = getattr(self.ctrl, "hotkeys", None)
        if not self.cfg.get("hotkeys_enabled", True):
            self.hk_status.setText("Raccourcis désactivés.")
        elif hk is None:
            self.hk_status.setText("⚠ Raccourcis non démarrés.")
        else:
            st = hk.status()
            self.hk_status.setText(("⚠ " if "Problèmes" in st else "✓ ") + st)
            if hk.last:
                from .panel import _pretty
                ago = int(time.time() - hk.last[1])
                self.hk_last.setText(f"Dernier raccourci reçu : <b>{_pretty(hk.last[0])}</b> "
                                     f"(il y a {ago} s) ✓")
