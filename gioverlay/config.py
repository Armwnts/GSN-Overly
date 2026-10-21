"""Configuration persistante de l'application (data/config.json)."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
# Les versions « application » (Mac et Windows) rangent les données de l'utilisateur
# (config, progression, carte) ailleurs que dans le code : variable GIO_DATA_DIR.
DATA_DIR = Path(os.environ["GIO_DATA_DIR"]) if os.environ.get("GIO_DATA_DIR") else APP_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"

# Groupes de points affichés. Les IDs viennent de l'arbre des labels de la
# map HoYoLAB (map_id=2, Teyvat). `python fetch_data.py --list-labels` les affiche.
DEFAULT_GROUPS = {
    "Coffres": {"labels": [17, 44, 45, 46, 269], "color": "#f5c542", "enabled": True},
    "Défis contre la montre": {"labels": [64], "color": "#b06cff", "enabled": True},
    "Coffres scellés / enterrés": {"labels": [75, 69], "color": "#ff9f43", "enabled": True},
    "Fées": {"labels": [18], "color": "#7fe3ff", "enabled": False},
    "Stèles élémentaires": {"labels": [70], "color": "#9be06b", "enabled": False},
    "Ennemis (récompense unique)": {"labels": [79], "color": "#ff5c5c", "enabled": False},
    "Oculus": {"labels": [5, 6, 194, 403, 508, 626, 694, 833], "color": "#ffffff", "enabled": False},
}

DEFAULTS = {
    "map_id": 2,
    "auto_dimension": True,        # change de dimension (Enkanomiya, le Gouffre…) quand la mini-map y est
    "lang": "fr-fr",
    # Zone de la mini-map à l'écran, en points (coordonnées macOS / Qt).
    # None tant que la calibration n'a pas été faite.
    "minimap_region": None,  # {"x":..,"y":..,"size":..}
    "fps": 10,
    "groups": DEFAULT_GROUPS,
    "show_layered_points": True,   # points d'étages / souterrains
    "fullmap_enabled": True,       # icônes sur la carte plein écran du jeu
    "icon_size": 16,
    "mark_distance_p0": 60,        # distance max (unités carte P0) pour "marquer le plus proche"
    "track_radius_factor": 4.0,    # rayon de recherche en suivi, en multiples du rayon de la mini-map
    "offset_map_px": [0.0, 0.0],   # correction fine si les points sont décalés
    "hotkeys_enabled": True,
    "hotkeys": {
        "toggle_overlay": "ctrl+alt+o",
        "mark_nearest": "ctrl+alt+m",
        "undo_mark": "ctrl+alt+z",
        "relocalize": "ctrl+alt+r",
        "mark_view": "ctrl+alt+v",
        "quest_help": "ctrl+alt+q",
    },
    # Aide de quête par IA : "gemini" (offre gratuite de Google, par défaut) ou "claude" (payant).
    "quest_provider": "gemini",
    "gemini_api_key": "",          # ou variable GEMINI_API_KEY
    "gemini_model": "",            # vide = choix automatique parmi les modèles gratuits
    "anthropic_api_key": "",       # ou variable ANTHROPIC_API_KEY
    "quest_model": "claude-sonnet-5-5",
    # Position de la carte d'aide (fractions de l'écran) : à droite du bouton « Entrée » du chat.
    "quest_card": {"left": 0.055, "bottom": 0.965, "width": 0.30},
    "macos_accessory_mode": True,
    # Paramètres (v6)
    "accent_color": "#F5C542",     # couleur d'accentuation de l'interface
    "uid": "",                     # UID Genshin (import de la vitrine, notes HoYoLAB)
    "hoyolab_cookies": {},         # ltuid_v2 / ltoken_v2… (restent sur cet ordinateur)
    "notes_enabled": True,         # notes en temps réel dans le panneau (si cookies)
    "resin_alert_on": True,
    "resin_alert": 200,            # me prévenir quand la résine atteint ce nombre
    "auto_checkin": True,          # connexion quotidienne HoYoLAB automatique
    "last_checkin": "",
    "diag_captures": True,
    "errors_notified_at": 0,
    "events_alert": True,
    "menubar_widget": True,
    "desk_widget": True,           # mini-widget sur le bureau (résine, argent du domaine, événements)
    "desk_widget_ontop": False,    # au-dessus des fenêtres (sinon au niveau du bureau)        # résine / argent du domaine / événements dans la barre des menus          # notification quand un événement se termine bientôt
    "notified_events": [],
    "dock_icon": True,             # icône dans le Dock (macOS)
    "auto_update": True,           # mises à jour automatiques         # captures d'écran automatiques quand quelque chose se passe mal  # nécessaire pour s'afficher au-dessus d'un jeu en plein écran
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "groups":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


OBSOLETE = ("learned_scale", "min_inliers", "lost_after_failures")


def load_config() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        try:
            cfg = _merge(DEFAULTS, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
            for k in OBSOLETE:
                cfg.pop(k, None)
            return cfg
        except (json.JSONDecodeError, OSError):
            pass
    return copy.deepcopy(DEFAULTS)


def save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def map_dir(map_id: int) -> Path:
    return DATA_DIR / "maps" / str(map_id)
