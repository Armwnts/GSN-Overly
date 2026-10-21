"""Couleur d'accentuation de l'interface (or par défaut), modifiable dans les Paramètres.

Le code écrit la couleur d'origine (#f5c542) dans ses feuilles de style ; ce module la remplace
au vol par la couleur choisie, y compris pour les fenêtres déjà ouvertes.
"""
from __future__ import annotations

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

BASE = "#f5c542"
PRESETS = [("Or", "#F5C542"), ("Hydro", "#4CC2F1"), ("Anémo", "#5FD4B0"), ("Électro", "#B98CF0"),
           ("Pyro", "#FF8A4C"), ("Cryo", "#9FE3F0"), ("Dendro", "#A5D23B"), ("Rose", "#FF7EB6"),
           ("Corail", "#FF6B6B"), ("Blanc", "#E8E6E3")]
_accent = BASE.upper()          # toujours en MAJUSCULES : les autres couleurs du code sont en minuscules
_installed = False
_orig_ss = QWidget.setStyleSheet
_orig_text = QLabel.setText


def accent() -> str:
    return _accent


def _fix(s: str) -> str:
    return s.replace(BASE, _accent) if isinstance(s, str) and BASE in s else s


def install(color: str | None = None):
    """À appeler une fois au démarrage, avant de créer les fenêtres."""
    global _installed, _accent
    if color and QColor(color).isValid():
        _accent = QColor(color).name().upper()
    if _installed:
        return
    _installed = True
    QWidget.setStyleSheet = lambda self, s: _orig_ss(self, _fix(s))
    QLabel.setText = lambda self, s: _orig_text(self, _fix(s))


def set_accent(color: str) -> str:
    """Change la couleur partout, y compris dans les fenêtres déjà ouvertes."""
    global _accent
    c = QColor(color)
    if not c.isValid():
        return _accent
    old, new = _accent, c.name().upper()
    if old == new:
        return new
    _accent = new
    for w in QApplication.allWidgets():
        ss = w.styleSheet()
        if old in ss:
            _orig_ss(w, ss.replace(old, new))
        if isinstance(w, QLabel):
            t = w.text()
            if old in t:
                _orig_text(w, t.replace(old, new))
    return new
