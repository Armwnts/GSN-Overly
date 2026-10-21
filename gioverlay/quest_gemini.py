"""Aide de quête gratuite : Gemini (offre gratuite de Google) + wiki Genshin.

1. Gemini lit la capture : titre de la quête suivie, objectif en cours.
2. L'application cherche la page de la quête sur le wiki Genshin Impact (gratuit).
3. Gemini rédige les étapes en français à partir du texte du wiki.

L'offre gratuite de Gemini ne comprend pas la recherche Google, d'où l'étape 2.
"""
from __future__ import annotations

import json
import os
import re

import numpy as np

from . import wiki
from .quest import QuestAnswer, QuestError, _jpeg_b64, parse_answer

# Du plus récent au plus léger : si un modèle n'existe plus ou que son quota gratuit
# du jour est épuisé, on passe au suivant.
MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
_last_good: str | None = None  # dernier modèle qui a répondu : essayé en premier la fois suivante

READ_PROMPT = """Capture d'écran de Genshin Impact (image 1) et zoom sur le haut à gauche (image 2).
Le suivi de quête est affiché en haut à gauche (sous la mini-map en jeu, ou sous l'avatar
quand la carte est ouverte) : un titre de quête, puis l'objectif en cours en dessous.
Réponds UNIQUEMENT avec ce JSON :
{"visible": true/false, "quest": "titre exact en français", "objective": "objectif en cours en français",
 "english_title": "ton meilleur guess du titre officiel anglais de cette quête (ou \\"\\")",
 "region": "région de Teyvat si identifiable (ou \\"\\")"}"""

ANSWER_SYSTEM = """Tu es un guide expert de Genshin Impact intégré dans un overlay de jeu.
Tu reçois la quête suivie par le joueur et des extraits du wiki Genshin Impact (en anglais).
Base-toi d'abord sur le wiki. Si le wiki ne couvre pas l'étape, dis-le et donne des conseils
généraux prudents, sans inventer de détails précis.
Réponds en français, au format EXACT suivant, sans markdown (pas de **, pas de #) :

QUÊTE : <titre de la quête>
ÉTAPE : <objectif en cours>
1. <action courte et concrète>
2. <action>
… (entre 3 et 8 étapes, une ligne chacune, max ~20 mots par ligne)
ASTUCE : <conseil si le joueur est bloqué : énigme, mécanisme, ennemi> (facultatif)

Concentre-toi sur l'objectif en cours et les étapes suivantes, pas sur toute la quête.
Pas de préambule."""


class GeminiQuestHelper:
    def __init__(self, api_key: str, model: str | None = None):
        try:
            from google import genai
            from google.genai import errors, types
        except ImportError as e:
            raise QuestError("La bibliothèque « google-genai » n'est pas installée.") from e
        self.types, self.errors = types, errors
        opts = None
        if os.environ.get("GIO_GEMINI_BASE_URL"):  # pour les tests
            opts = types.HttpOptions(base_url=os.environ["GIO_GEMINI_BASE_URL"])
        self.client = genai.Client(api_key=api_key, http_options=opts)
        first = model or _last_good
        self.models = ([first] if first else []) + [m for m in MODELS if m != first]

    # --- appel au modèle, avec repli sur le modèle suivant --------------------------
    def _generate(self, contents, system: str | None = None, json_mode=False) -> str:
        cfg = self.types.GenerateContentConfig(
            system_instruction=system, temperature=0.2,
            response_mime_type="application/json" if json_mode else None)
        last = None
        for m in self.models:
            try:
                r = self.client.models.generate_content(model=m, contents=contents, config=cfg)
                text = (r.text or "").strip()
                if text:
                    global _last_good
                    _last_good = m
                    if self.models[0] != m:
                        self.models.remove(m)
                        self.models.insert(0, m)
                    return text
                last = QuestError("Réponse vide de Gemini.")
            except self.errors.ClientError as e:
                msg = str(e).lower()
                if e.code == 400 and ("api key" in msg or "api_key" in msg):
                    raise QuestError("Clé API Gemini refusée : vérifie-la dans « Clé API Gemini… ».") from e
                if e.code in (401, 403):
                    raise QuestError("Clé API Gemini non autorisée (projet ou clé désactivé ?).") from e
                if e.code in (404, 429):   # modèle indisponible ou quota gratuit épuisé
                    last = e
                    continue
                raise QuestError(f"Requête refusée par Gemini ({e.code}).") from e
            except self.errors.ServerError as e:
                last = e
                continue
            except Exception as e:  # réseau (httpx), délai dépassé…
                if "connect" in type(e).__name__.lower() or "timeout" in type(e).__name__.lower():
                    raise QuestError("Pas de connexion à Gemini (Internet ?).") from e
                raise
        if isinstance(last, self.errors.ClientError) and last.code == 429:
            raise QuestError("Limite gratuite de Gemini atteinte pour le moment : réessaie un peu plus tard.")
        raise QuestError("Gemini est indisponible pour le moment. Réessaie plus tard.")

    # --- pipeline ----------------------------------------------------------------------
    def ask(self, screen_bgr: np.ndarray, question: str = "", on_status=lambda s: None) -> QuestAnswer:
        t = self.types
        h, w = screen_bgr.shape[:2]
        crop = screen_bgr[int(h * 0.04):int(h * 0.55), 0:int(w * 0.36)]
        import base64
        imgs = [t.Part.from_bytes(data=base64.b64decode(_jpeg_b64(img, side)), mime_type="image/jpeg")
                for img, side in ((screen_bgr, 1568), (crop, 1200))]

        on_status("Lecture de la quête…")
        info = _parse_json(self._generate([*imgs, READ_PROMPT], json_mode=True))
        if not info.get("visible", True) or not info.get("quest"):
            return QuestAnswer(quest="Aucune quête suivie visible",
                               lines=["Ouvre le journal de quêtes (J), choisis une quête et clique sur « Suivre ».",
                                      "Le titre doit être visible en haut à gauche de l'écran."])
        quest, objective = info.get("quest", ""), info.get("objective", "")

        on_status(f"Recherche de « {quest} » sur le wiki…")
        queries = [f'"{quest}"', info.get("english_title", ""), quest]
        pages = wiki.find_quest_pages([q for q in queries if q and q != '""'])

        on_status("Rédaction des étapes…")
        ctx = "\n\n".join(f"=== Page du wiki : {p['title']} ===\n{p['text']}" for p in pages) \
            or "(Aucune page du wiki trouvée pour cette quête.)"
        prompt = (f"Quête suivie : {quest}\nObjectif en cours : {objective}\n"
                  f"Région : {info.get('region', '')}\n")
        if question.strip():
            prompt += f"Le joueur précise : « {question.strip()} »\n"
        prompt += f"\nExtraits du wiki :\n{ctx}"
        ans = parse_answer(self._generate([prompt], system=ANSWER_SYSTEM))
        ans.quest = ans.quest or quest
        ans.step = ans.step or objective
        ans.sources = ["wiki Genshin (Fandom)"] if pages else ["réponse non vérifiée (pas de page wiki trouvée)"]
        return ans


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        d = json.loads(text)
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


BUILD_SYSTEM = """Tu es un expert des builds de Genshin Impact, intégré dans l'application du joueur.
Réponds en français, de façon concise et concrète (listes courtes : armes, sets d'artéfacts,
statistiques principales et secondaires, priorités d'aptitudes, équipes).
{context}
Si tu complètes avec tes propres connaissances, dis-le clairement (le jeu évolue, des
informations récentes peuvent te manquer). N'invente pas de chiffres précis."""


def build_chat(api_key: str, turns: list[tuple[str, str]], guide: str = "", image_jpeg: bytes | None = None,
               model: str | None = None, player: str = "") -> str:
    """Conversation avec Gemini sur les builds. `turns` = [("user"|"model", texte), …]."""
    h = GeminiQuestHelper(api_key, model)
    t = h.types
    if guide:
        context = ("Voici le guide du personnage publié par sephijin.fr (texte), et l'image de synthèse "
                   "du build jointe au dernier message. Base-toi d'abord sur ce guide et cite-le "
                   "(« d'après sephijin.fr… »).\n\n" + guide)
    else:
        context = "Pas de guide sephijin.fr pour cette question : réponds avec tes connaissances générales."
    if player:
        context += ("\n\nInformations données par le joueur sur son compte (tiens-en compte, sans les "
                    "répéter inutilement) :\n" + player)
    contents = []
    last_user = max((i for i, (r, _) in enumerate(turns) if r == "user"), default=-1)
    for i, (role, text) in enumerate(turns):
        parts = [t.Part.from_text(text=text)]
        if i == last_user and image_jpeg:
            parts.insert(0, t.Part.from_bytes(data=image_jpeg, mime_type="image/jpeg"))
        contents.append(t.Content(role="user" if role == "user" else "model", parts=parts))
    return h._generate(contents, system=BUILD_SYSTEM.format(context=context))
