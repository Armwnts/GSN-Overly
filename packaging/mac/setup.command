#!/bin/bash
# Genshin Overlay : installation de Python et des bibliothèques (première ouverture ou mise à jour).
SUP="$HOME/Library/Application Support/GenshinOverlay"
RES="$(cat "$SUP/resources_path")"
CODE="$(cat "$SUP/code_path" 2>/dev/null)"; [ -n "$CODE" ] || CODE="$RES/app"
RT="$(cat "$SUP/runtime_path" 2>/dev/null)"; [ -n "$RT" ] || RT="$SUP/runtime"
APP="$RES/app"
DATA="$SUP/data"
mkdir -p "$RT/bin" "$DATA/maps/2"
exec > >(tee "$DATA/setup.log") 2>&1

fail() {
  echo
  echo "  ERREUR : $1"
  echo "  Vérifie ta connexion Internet, puis rouvre Genshin Overlay."
  echo "  (Détails : $DATA/setup.log)"
  echo
  read -r -p "  Appuie sur Entrée pour fermer cette fenêtre." _
  exit 1
}

clear
echo
echo "  ┌─────────────────────────────────────────────────────────┐"
echo "  │  Genshin Overlay — installation (5 à 10 min)            │"
echo "  │  Ne ferme pas cette fenêtre.                            │"
echo "  └─────────────────────────────────────────────────────────┘"
echo

case "$(uname -m)" in
  arm64)
    URL="https://files.pythonhosted.org/packages/9e/d4/d42eabbd9b61a965e0a262e06bf8452744593e7a3110a5c4273c4e917729/uv-0.13.0-py3-none-macosx_11_0_arm64.whl"
    SHA="a5f93ae362b751737cecef0bbc0294624be68a843ff9b32d86e6862a26101630" ;;
  x86_64)
    URL="https://files.pythonhosted.org/packages/ff/27/2a2dde8a643337b6746a8e632b3ba8aef7c82b9bc28642bb94dd3a046ef5/uv-0.13.0-py3-none-macosx_10_12_x86_64.whl"
    SHA="93265932cb2efd1486d4d182211281b3f0b31637e2fc160c4c8d7b15294188d1" ;;
  *) fail "processeur non reconnu : $(uname -m)" ;;
esac

UV="$RT/bin/uv"
if [ ! -x "$UV" ]; then
  echo "[1/4] Téléchargement de l'installateur (uv)…"
  curl -fL --retry 3 --progress-bar -o "$RT/uv.whl" "$URL" || fail "téléchargement de uv impossible"
  [ "$(shasum -a 256 "$RT/uv.whl" | cut -d' ' -f1)" = "$SHA" ] || fail "fichier uv corrompu"
  unzip -o -j -q "$RT/uv.whl" 'uv-*.data/scripts/uv' -d "$RT/bin" || fail "décompression de uv impossible"
  chmod +x "$UV"
  rm -f "$RT/uv.whl"
fi

export UV_PYTHON_INSTALL_DIR="$RT/python"
export UV_CACHE_DIR="$RT/cache"
export UV_PYTHON_PREFERENCE=only-managed

if [ ! -x "$RT/venv/bin/python" ]; then
  echo "[2/4] Installation de Python 3.12 (privé, n'affecte pas le reste du Mac)…"
  "$UV" python install 3.12 || fail "installation de Python impossible"
  "$UV" venv --python 3.12 "$RT/venv" || fail "création de l'environnement impossible"
else
  echo "[2/4] Python déjà installé."
fi

echo "[3/4] Installation des bibliothèques (PyQt6, OpenCV…) — environ 200 Mo la première fois…"
"$UV" pip install --python "$RT/venv/bin/python" -r "$CODE/requirements.txt" || fail "installation des bibliothèques impossible"

echo "[4/4] Copie de la carte de Teyvat…"
for f in map_gray.png meta.json points.json labels.json preview.jpg; do
  cp -f "$APP/data/maps/2/$f" "$DATA/maps/2/$f" || fail "copie de la carte impossible"
done
cp -f "$APP"/data/maps/2/features_*.npz "$DATA/maps/2/" 2>/dev/null
mkdir -p "$DATA/maps/2/icons" && cp -f "$APP"/data/maps/2/icons/*.png "$DATA/maps/2/icons/"
xattr -dr com.apple.quarantine "$DATA" 2>/dev/null

rm -rf "$RT/cache"
shasum -a 256 "$CODE/requirements.txt" | cut -c1-16 > "$RT/.ready"

echo
echo "  ✔ Installation terminée ! Genshin Overlay démarre."
echo "  Tu peux fermer cette fenêtre."
echo
open -b fr.armand.genshinoverlay.app
