"""Mises à jour automatiques.

Un petit fichier « manifeste » (un Google Doc partagé par lien) annonce la dernière version :
numéro, notes, liste complète des fichiers de code avec leur empreinte SHA-256, et l'adresse
d'un « patch » (les fichiers modifiés depuis la version installée par le .dmg). Le tout est
signé avec la clé de l'auteur (Ed25519) : une mise à jour qui n'a pas été signée par lui,
ou dont un seul fichier ne correspond pas, est refusée.

La nouvelle version est installée dans ~/Library/Application Support/GenshinOverlay/code/<version>
et utilisée au prochain lancement (le lanceur revient tout seul à la version d'origine si elle
plante au démarrage).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tarfile
from pathlib import Path

import requests

from .ed25519 import verify

log = logging.getLogger("gio.maj")
HERE = Path(__file__).resolve().parent.parent          # dossier du code en cours d'exécution
HEADERS = {"User-Agent": "GenshinOverlay-updater"}


class UpdateError(RuntimeError):
    pass


def _vt(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def current_version() -> str:
    try:
        return (HERE / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0"


def channel() -> dict:
    try:
        return json.loads((HERE / "channel.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def support_dir() -> Path | None:
    p = os.environ.get("GIO_SUP")
    return Path(p) if p else None


def base_dir() -> Path:
    """Code d'origine livré dans l'application (le .dmg)."""
    p = os.environ.get("GIO_APP_RES")
    return Path(p) if p else HERE


def canonical(manifest: dict) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _parse_manifest(text: str) -> dict:
    text = text.lstrip("﻿")
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < i:
        raise UpdateError("Manifeste de mise à jour illisible.")
    # Google Docs peut remplacer les guillemets droits par des guillemets typographiques
    raw = text[i:j + 1].replace("“", '"').replace("”", '"')
    try:
        return json.loads(raw)
    except ValueError as e:
        raise UpdateError("Manifeste de mise à jour illisible.") from e


class Updater:
    def __init__(self):
        self.ch = channel()
        self.s = requests.Session()
        self.s.headers.update(HEADERS)

    @property
    def configured(self) -> bool:
        return bool(self.ch.get("manifest_url") and self.ch.get("public_key") and support_dir())

    def fetch_manifest(self) -> dict:
        try:
            r = self.s.get(self.ch["manifest_url"], timeout=20)
        except requests.RequestException as e:
            raise UpdateError("Serveur de mises à jour injoignable (Internet ?).") from e
        if r.status_code != 200 or "<html" in r.text[:200].lower():
            raise UpdateError("Le fichier des mises à jour n'est pas accessible (lien de partage ?).")
        m = _parse_manifest(r.text)
        try:
            sig = base64.b64decode(m.get("sig", ""))
            key = base64.b64decode(self.ch["public_key"])
        except ValueError as e:
            raise UpdateError("Signature de mise à jour invalide.") from e
        if not verify(key, canonical(m), sig):
            raise UpdateError("Signature de mise à jour invalide : mise à jour refusée.")
        return m

    def check(self) -> dict | None:
        """Renvoie le manifeste si une version plus récente existe (et n'est pas déjà installée)."""
        m = self.fetch_manifest()
        cur = current_version()
        if _vt(m.get("version", "0")) <= _vt(cur):
            return None
        sup = support_dir()
        if sup and (sup / "code" / m["version"] / "run.py").exists():
            return None   # déjà téléchargée, en attente de redémarrage
        bad = (sup / "code" / "bad").read_text().split() if sup and (sup / "code" / "bad").exists() else []
        if m["version"] in bad:
            return None   # cette version a planté au démarrage : on ne la réinstalle pas
        return m

    def install(self, m: dict) -> Path:
        sup = support_dir()
        if sup is None:
            raise UpdateError("Mises à jour indisponibles dans cette installation.")
        files: dict = m["files"]
        patch: dict[str, bytes] = {}
        if m.get("patch"):
            try:
                r = self.s.get(m["patch"]["url"], timeout=120)
                r.raise_for_status()
            except requests.RequestException as e:
                raise UpdateError("Téléchargement de la mise à jour impossible.") from e
            data = r.content
            if hashlib.sha256(data).hexdigest() != m["patch"]["sha256"]:
                raise UpdateError("Fichier de mise à jour abîmé (empreinte différente).")
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                for mem in tf.getmembers():
                    if mem.isfile() and mem.name in files:
                        patch[mem.name] = tf.extractfile(mem).read()
        base = base_dir()
        out = sup / "code" / m["version"]
        tmp = sup / "code" / (m["version"] + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        for rel, sha in files.items():
            if ".." in Path(rel).parts or Path(rel).is_absolute():
                raise UpdateError(f"Chemin refusé dans la mise à jour : {rel}")
            data = patch.get(rel)
            if data is None:
                src = base / rel
                if not src.exists():
                    raise UpdateError(f"Fichier manquant pour la mise à jour : {rel}")
                data = src.read_bytes()
            if hashlib.sha256(data).hexdigest() != sha:
                raise UpdateError(f"Fichier non conforme : {rel}")
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
        shutil.rmtree(out, ignore_errors=True)
        tmp.rename(out)
        (sup / "code" / "current").write_text(m["version"], encoding="utf-8")
        # on ne garde que les 2 dernières versions téléchargées
        olds = sorted((p for p in (sup / "code").iterdir() if p.is_dir() and not p.name.endswith(".tmp")),
                      key=lambda p: _vt(p.name))
        for p in olds[:-2]:
            shutil.rmtree(p, ignore_errors=True)
        log.info("Mise à jour %s installée dans %s", m["version"], out)
        return out


def restart_app():
    """Relance l'application (nouvelle version) puis quitte celle-ci."""
    import subprocess
    import sys
    bid = os.environ.get("GIO_BUNDLE_ID")
    if sys.platform == "darwin" and bid:
        subprocess.Popen(["/bin/sh", "-c", f"sleep 2; open -b {bid}"])
    elif sys.platform == "win32" and support_dir():
        # le démarreur Windows (app\run.py) relance l'application quand elle se ferme
        try:
            (support_dir() / "code" / ".restart").write_text("1")
        except OSError:
            pass
    from PyQt6.QtWidgets import QApplication
    QApplication.quit()
