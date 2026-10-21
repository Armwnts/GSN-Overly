#!/usr/bin/env python3
"""Lance l'overlay. (Télécharge d'abord les données : python fetch_data.py)"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # nécessaire avec le Python « embarquable » de Windows

# Sans console (application lancée par un double-clic), on écrit les messages
# et les erreurs dans data/overlay.log.
if sys.stdout is None or sys.stderr is None:
    _data = os.environ.get("GIO_DATA_DIR") or os.path.join(HERE, "data")
    os.makedirs(_data, exist_ok=True)
    _log = open(os.path.join(_data, "overlay.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

from gioverlay.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
