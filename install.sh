#!/bin/bash
# Installation sur macOS : crée un environnement Python isolé et installe les dépendances.
set -e
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null; then
  echo "Python 3 introuvable. Installe-le depuis https://www.python.org/downloads/macos/ puis relance."
  exit 1
fi
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt
echo
echo "✔ Installation terminée."
echo "Étape suivante :  source .venv/bin/activate && python fetch_data.py"
