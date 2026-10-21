#!/usr/bin/env python3
"""Outils de publication de Genshin Overlay (à lancer par Claude, pas par l'app).

  release.py files   CODE_DIR                         -> empreintes des fichiers de code (JSON)
  release.py base    CODE_DIR OUT_DIR                 -> base-<v>.json + base-<v>.tar.xz (code livré dans le .dmg)
  release.py patch   CODE_DIR BASE_JSON OUT.tar.xz    -> fichiers modifiés depuis la base
  release.py manifest CODE_DIR BASE_VERSION NOTES [PATCH_FILE PATCH_URL] -> manifeste signé (JSON)
  release.py keygen                            -> crée keys/update_ed25519.pem et affiche la clé publique

Clé privée : packaging/keys/update_ed25519.pem (ne JAMAIS la publier ni la mettre dans l'app ;
le dossier keys/ est ignoré par git). La clé publique va dans channel.json.
"""
import base64
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOP = ("run.py", "requirements.txt", "VERSION", "channel.json")


def code_files(code: Path) -> dict:
    out = {}
    for rel in TOP:
        if (code / rel).exists():
            out[rel] = hashlib.sha256((code / rel).read_bytes()).hexdigest()
    for p in sorted((code / "gioverlay").rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            rel = p.relative_to(code).as_posix()
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def canonical(m: dict) -> bytes:
    body = {k: v for k, v in m.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(m: dict) -> dict:
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key((HERE / "keys" / "update_ed25519.pem").read_bytes(), None)
    m = dict(m)
    m["sig"] = base64.b64encode(key.sign(canonical(m))).decode()
    return m


def make_tar(code: Path, rels, out: Path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz", preset=9) as tf:
        for rel in sorted(rels):
            data = (code / rel).read_bytes()
            ti = tarfile.TarInfo(rel)
            ti.size = len(data)
            ti.mtime = 0
            ti.mode = 0o644
            tf.addfile(ti, io.BytesIO(data))
    out.write_bytes(buf.getvalue())


def main(argv):
    cmd = argv[1]
    if cmd == "keygen":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        kd = HERE / "keys"
        kd.mkdir(exist_ok=True)
        if (kd / "update_ed25519.pem").exists():
            raise SystemExit("Une clé existe déjà : keys/update_ed25519.pem")
        k = Ed25519PrivateKey.generate()
        (kd / "update_ed25519.pem").write_bytes(k.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (kd / "update_ed25519.pem").chmod(0o600)
        pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        print("Clé publique (à mettre dans channel.json) :", base64.b64encode(pub).decode())
        return
    if cmd == "files":
        print(json.dumps(code_files(Path(argv[2])), indent=1))
    elif cmd == "base":
        code, out = Path(argv[2]), Path(argv[3])
        out.mkdir(parents=True, exist_ok=True)
        v = (code / "VERSION").read_text().strip()
        files = code_files(code)
        (out / f"base-{v}.json").write_text(json.dumps({"version": v, "files": files}, indent=1))
        make_tar(code, files, out / f"base-{v}.tar.xz")
        print(out / f"base-{v}.json")
    elif cmd == "patch":
        code, base, out = Path(argv[2]), json.loads(Path(argv[3]).read_text()), Path(argv[4])
        files = code_files(code)
        changed = [r for r, h in files.items() if base["files"].get(r) != h]
        make_tar(code, changed, out)
        print(json.dumps({"changed": changed, "bytes": out.stat().st_size, "sha256":
                          hashlib.sha256(out.read_bytes()).hexdigest()}, indent=1))
    elif cmd == "manifest":
        code, base_v, notes = Path(argv[2]), argv[3], argv[4]
        m = {"version": (code / "VERSION").read_text().strip(), "base": base_v, "notes": notes,
             "files": code_files(code)}
        if len(argv) > 6:
            pf, url = Path(argv[5]), argv[6]
            m["patch"] = {"url": url, "sha256": hashlib.sha256(pf.read_bytes()).hexdigest()}
        print(json.dumps(sign(m), ensure_ascii=False, separators=(",", ":")))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
