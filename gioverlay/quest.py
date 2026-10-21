"""Aide de quête par IA (Claude, API d'Anthropic).

Une capture de l'écran est envoyée à Claude, qui :
1. lit le titre et l'objectif de la quête suivie (en haut à gauche de l'écran),
2. cherche une soluce sur le web (outil de recherche web de l'API),
3. renvoie des étapes courtes, affichées en overlay en bas à gauche.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import cv2
import numpy as np

DEFAULT_MODEL = "claude-sonnet-5-5"
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 4}

SYSTEM = """Tu es un guide expert de Genshin Impact intégré dans un overlay de jeu.
On t'envoie une capture d'écran du joueur. Ton travail :
1. Lis le titre EXACT de la quête suivie et son objectif en cours. Ils sont affichés en haut
   à gauche de l'écran (sous la mini-map en jeu, ou sous l'avatar quand la carte est ouverte).
2. Utilise la recherche web pour trouver la soluce de CETTE étape précise (wiki Genshin Impact
   Fandom, Game8, sites de guides français, etc.). Ne te fie pas à ta mémoire pour les détails :
   vérifie, car les quêtes récentes te sont souvent inconnues.
3. Réponds en français, au format EXACT suivant, sans markdown (pas de **, pas de #) :

QUÊTE : <titre de la quête>
ÉTAPE : <objectif en cours>
1. <action courte et concrète>
2. <action>
… (entre 3 et 8 étapes, une ligne chacune, max ~20 mots par ligne)
ASTUCE : <conseil utile si le joueur est bloqué : énigme, mécanisme, ennemi> (facultatif)

Sois concret : lieux, directions, éléments à utiliser, ordre des actions, solution des énigmes.
Pas de préambule, pas de phrase du type « Je vais chercher… » dans la réponse finale.
Si aucune quête n'est visible à l'écran, réponds seulement :
QUÊTE : introuvable
Puis une phrase expliquant au joueur d'afficher le suivi de quête (touche J, puis « Suivre »)."""


@dataclass
class QuestAnswer:
    quest: str = ""
    step: str = ""
    lines: list[str] = field(default_factory=list)
    tip: str = ""
    sources: list[str] = field(default_factory=list)
    raw: str = ""


class QuestError(RuntimeError):
    pass


def _jpeg_b64(img: np.ndarray, max_side: int, quality=85) -> str:
    h, w = img.shape[:2]
    k = min(1.0, max_side / max(h, w))
    if k < 1:
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise QuestError("Impossible d'encoder la capture d'écran.")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def build_images(screen_bgr: np.ndarray) -> list[dict]:
    """Écran complet (réduit) + zoom sur la zone du suivi de quête (lisibilité du texte)."""
    h, w = screen_bgr.shape[:2]
    crop = screen_bgr[int(h * 0.04):int(h * 0.55), 0:int(w * 0.36)]
    out = []
    for img, side in ((screen_bgr, 1568), (crop, 1200)):
        out.append({"type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(img, side)}})
    return out


def parse_answer(text: str) -> QuestAnswer:
    text = re.sub(r"\*\*|__|^#+\s*", "", text.strip(), flags=re.M)
    a = QuestAnswer(raw=text)
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        up = s.upper()
        if up.startswith(("QUÊTE", "QUETE")) and ":" in s:
            a.quest = s.split(":", 1)[1].strip()
        elif up.startswith(("ÉTAPE", "ETAPE")) and ":" in s:
            a.step = s.split(":", 1)[1].strip()
        elif up.startswith("ASTUCE") and ":" in s:
            a.tip = s.split(":", 1)[1].strip()
        else:
            a.lines.append(s)
    return a


def _sources(content) -> list[str]:
    """Noms de domaine des pages citées (à afficher, comme l'exige l'API)."""
    seen: list[str] = []
    for b in content:
        for c in getattr(b, "citations", None) or []:
            url = getattr(c, "url", None)
            if url:
                d = urlparse(url).netloc.removeprefix("www.")
                if d and d not in seen:
                    seen.append(d)
    return seen[:4]


class QuestHelper:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        try:
            import anthropic
        except ImportError as e:
            raise QuestError("La bibliothèque « anthropic » n'est pas installée.") from e
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120)
        self.model = model

    def ask(self, screen_bgr: np.ndarray, question: str = "", on_status=lambda s: None) -> QuestAnswer:
        a = self._anthropic
        prompt = "Voici mon écran de jeu (image 1) et un zoom sur le haut à gauche (image 2)."
        if question.strip():
            prompt += f"\nPrécision du joueur : « {question.strip()} »"
        prompt += "\nAide-moi à faire l'étape en cours de ma quête."
        messages = [{"role": "user", "content": [*build_images(screen_bgr), {"type": "text", "text": prompt}]}]
        try:
            on_status("Lecture de la quête…")
            for _ in range(4):  # « pause_turn » : on renvoie le tour pour que Claude continue
                with self.client.messages.stream(model=self.model, max_tokens=1500, system=SYSTEM,
                                                 messages=messages, tools=[WEB_SEARCH_TOOL]) as stream:
                    for ev in stream:
                        if ev.type == "content_block_start" and ev.content_block.type == "server_tool_use":
                            on_status("Recherche de la soluce sur le web…")
                    msg = stream.get_final_message()
                if msg.stop_reason != "pause_turn":
                    break
                messages = messages[:1] + [{"role": "assistant", "content": msg.content}]
        except a.AuthenticationError as e:
            raise QuestError("Clé API refusée : vérifie-la dans « Clé API Claude… ».") from e
        except a.PermissionDeniedError as e:
            raise QuestError("Accès refusé par l'API (recherche web désactivée sur ton compte ?).") from e
        except a.RateLimitError as e:
            raise QuestError("Trop de demandes d'un coup : réessaie dans une minute.") from e
        except a.BadRequestError as e:
            m = str(e)
            if "credit" in m.lower() or "billing" in m.lower():
                raise QuestError("Crédit API épuisé : recharge ton compte sur console.anthropic.com.") from e
            raise QuestError(f"Requête refusée : {m[:200]}") from e
        except a.APIConnectionError as e:
            raise QuestError("Pas de connexion à l'API Claude (Internet ?).") from e
        except a.APIStatusError as e:
            raise QuestError(f"Erreur de l'API Claude ({e.status_code}). Réessaie plus tard.") from e

        # Réponse finale = texte écrit après la dernière recherche.
        last_tool = max((i for i, b in enumerate(msg.content) if b.type == "web_search_tool_result"), default=-1)
        final = [b for b in msg.content[last_tool + 1:] if b.type == "text"]
        text = "".join(b.text for b in final).strip()
        if not text:
            raise QuestError("L'IA n'a pas renvoyé de réponse. Réessaie.")
        ans = parse_answer(text)
        ans.sources = _sources(msg.content)
        return ans
