# Genshin Overlay

Overlay non officiel pour **Genshin Impact** (macOS, Windows expérimental) : affiche les **coffres, défis, oculus…** de la carte interactive HoYoLAB directement **sur la mini-map** et sur la **carte du jeu en plein écran**, avec suivi de ta progression.

L'app ne touche ni à la mémoire ni aux fichiers du jeu : elle **lit seulement l'écran** (capture de la mini-map), retrouve ta position sur la carte, puis dessine les points par-dessus. Elle marche aussi avec **GeForce NOW**.

## Fonctionnalités

- **Mini-map** : coffres, défis contre la montre, fées, stèles, oculus… restants autour de toi ; « Marquer fait », annuler, masquer (raccourcis clavier globaux).
- **Carte du jeu en plein écran** : les points restants s'affichent sur la carte ouverte (M).
- **Dimensions** : Teyvat, Enkanomiya, Mines souterraines du Gouffre, Mer d'antan, Montagne sacrée ancienne, Temple de l'espace, Lune de givre (téléchargées depuis HoYoLAB au premier choix, détection automatique).
- **Grande fenêtre** : carte interactive (suivi du joueur), **Builds**, **News** (codes, mises à jour, persos annoncés), **Mes persos** (import Enka par UID, matériaux d'élévation), **Compte** (exploration officielle, journal du voyageur, calendrier des événements), **Paramètres** (couleur d'accentuation, raccourcis…).
- **HoYoLAB** (avec tes cookies) : notes en temps réel (résine, commissions, expéditions, argent du domaine), alerte résine, **connexion quotidienne automatique**, alerte « événement bientôt fini ».
- **Mini-widget** sur le bureau (résine, argent du domaine, événements) : l'app peut tourner en arrière-plan.
- **Aide de quête par IA** (Gemini, clé gratuite) à partir d'une capture.
- **Passer les dialogues** (appuie sur F pendant les dialogues) — voir l'avertissement plus bas.
- **Diagnostic** : journal des erreurs (jamais effacées avant envoi), captures en cas de problème, rapport en un clic (sans clé ni cookies).
- **Mises à jour automatiques signées** (Ed25519), si un canal est configuré (voir plus bas).

## Installer depuis les sources

### macOS

```bash
./install.sh                      # crée .venv et installe les dépendances
source .venv/bin/activate
python fetch_data.py              # télécharge la carte de Teyvat et les points (quelques minutes)
python run.py
```

Autorise ensuite le Terminal (ou l'app) dans **Réglages Système → Confidentialité et sécurité** : *Enregistrement de l'écran* (obligatoire) et *Accessibilité* (raccourcis, passage des dialogues). Dans le jeu, panneau → Outils → **Calibrer la mini-map**.

### Windows (expérimental)

```bat
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python fetch_data.py
.venv\Scripts\python run.py
```

Mets le jeu en mode *Fenêtré* ou *Plein écran fenêtré*. Lance en administrateur pour que l'appui sur F fonctionne (le jeu tourne lui-même en administrateur).

## Construire les applications

```bash
# macOS : « Genshin Overlay.app » + GenshinOverlay-<version>.dmg
packaging/mac/build_app.sh . data/maps packaging/mac/AppIcon.icns out

# Windows : GenshinOverlay-Windows-<version>.zip
python packaging/windows/build_zip.py . data/maps out
```

Les applications construites ne contiennent **aucune donnée personnelle** : la progression, les réglages, la clé Gemini et les cookies HoYoLAB sont rangés sur l'ordinateur de chaque joueur (`~/Library/Application Support/GenshinOverlay` sur Mac, `%LOCALAPPDATA%\GenshinOverlay` sur Windows).

## Mises à jour automatiques (optionnel)

Dans ce dépôt, `channel.json` est vide : les mises à jour automatiques sont désactivées. Pour les activer :

1. `python packaging/release.py keygen` → crée `packaging/keys/update_ed25519.pem` (**ne jamais publier**) et affiche la clé publique.
2. Mets la clé publique et l'adresse d'un fichier texte public (le « manifeste ») dans `channel.json`.
3. Pour chaque version : `release.py base` (une fois), `release.py patch`, publie le patch, puis `release.py manifest` et remplace le contenu du manifeste. L'app refuse toute mise à jour qui n'est pas signée par ta clé.

## Données et crédits

- Carte, points et catégories : carte interactive **HoYoLAB** (HoYoverse).
- Vitrine des personnages : **Enka.Network**. Données des personnages et matériaux : **Project Yatta**. Builds : **sephijin.fr** (lus en direct, avec lien vers la source).
- IA : **Google Gemini** (clé personnelle de chaque joueur).

## ⚠️ Avertissement

Projet de fan, **non officiel et non affilié à HoYoverse**. Genshin Impact et les éléments du jeu appartiennent à HoYoverse.

L'overlay ne lit que l'écran, mais **« Passer les dialogues » appuie sur des touches à ta place** : c'est une automatisation contraire aux conditions d'utilisation du jeu. Elle est désactivée par défaut ; utilise-la à tes risques.
