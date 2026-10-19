"""Accès à Google Gemini : recherche web (Google Search) et extraction structurée.

L'offre gratuite de Gemini suffit pour une collecte quotidienne. Clé : https://aistudio.google.com/apikey
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date

from pydantic import ValidationError

from .llm import CONSIGNE_EXTRACTION, IAIndisponible, _lots
from .models import ExtractionResultat, EvenementExtrait, RawDocument

log = logging.getLogger(__name__)

PAUSE_ENTRE_APPELS = 5      # secondes : respecte la limite de requêtes par minute de l'offre gratuite
ESSAIS_SI_SATURE = 3

FORMAT_JSON = """
Réponds UNIQUEMENT avec un objet JSON de la forme :
{"evenements": [{
  "titre": str, "type": "formation|conference|seminaire|atelier|forum|salon|rencontre|autre",
  "themes": ["entrepreneuriat|finance|numerique|marketing|management|marches_publics|developpement_personnel|diplomatie|autre"],
  "resume": str, "date_debut": "AAAA-MM-JJ"|null, "date_fin": "AAAA-MM-JJ"|null, "horaires": str|null,
  "lieu": str|null, "ville": str|null, "organisateur": str|null, "public_cible": str|null,
  "tarif": str|null, "tarif_fcfa": int|null, "gratuit": bool|null, "inscription_url": str|null,
  "date_limite_inscription": "AAAA-MM-JJ"|null, "contact": str|null,
  "source_officielle": bool, "source_urls": [str]
}]}"""


def _client():
    from google import genai

    cle = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return genai.Client(api_key=cle)


_modele_actif: str | None = None  # modèle de remplacement retenu pendant l'exécution


def _modele_suggere(message: str) -> str | None:
    """Google indique parfois le modèle qui remplace un modèle retiré (« use models/xxx »)."""
    m = re.search(r"use (?:models/)?(gemini-[\w.\-]+)", message or "")
    return m.group(1) if m else None


def _appeler(client, essais: int = ESSAIS_SI_SATURE, **kwargs):
    """Appel avec reprise quand l'offre gratuite est saturée (429/503) ou quand le modèle a été retiré (404)."""
    global _modele_actif
    from google.genai import errors

    if _modele_actif:
        kwargs["model"] = _modele_actif
    essai = 0
    while essai < essais:
        essai += 1
        try:
            return client.models.generate_content(**kwargs)
        except errors.APIError as exc:
            suggestion = _modele_suggere(exc.message) if exc.code == 404 else None
            if suggestion and suggestion != kwargs["model"]:
                log.warning("Modèle %s retiré par Google : passage à %s (mettez à jour modele_gemini).",
                            kwargs["model"], suggestion)
                kwargs["model"] = _modele_actif = suggestion
                essai -= 1  # changer de modèle ne compte pas comme un essai
                continue
            if exc.code in (429, 500, 503) and essai < essais:
                attente = 30 * essai
                log.info("Gemini saturé (%s), nouvel essai dans %d s", exc.code, attente)
                time.sleep(attente)
                continue
            raise IAIndisponible(f"Gemini {exc.code} : {exc.message}") from exc
        except Exception as exc:  # réseau, DNS…
            raise IAIndisponible(f"Gemini injoignable : {exc}") from exc


def rechercher(consigne: str, modele: str) -> tuple[str, list[str]]:
    """Recherche web via l'outil Google Search de Gemini. Renvoie (synthèse, URL sources)."""
    from google.genai import types

    # Un seul essai : si l'outil de recherche n'a pas de quota dans l'offre gratuite, inutile d'attendre.
    reponse = _appeler(
        _client(),
        essais=1,
        model=modele,
        contents=consigne,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    texte = (reponse.text or "").strip()
    sources: list[str] = []
    for candidat in reponse.candidates or []:
        meta = candidat.grounding_metadata
        for chunk in (meta.grounding_chunks or []) if meta else []:
            if chunk.web and chunk.web.uri:
                sources.append(chunk.web.uri)
    return texte, list(dict.fromkeys(sources))


def _lire_json(texte: str) -> ExtractionResultat | None:
    texte = texte.strip()
    if texte.startswith("```"):
        texte = texte.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(texte)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        data = {"evenements": data}
    valides = []
    for item in data.get("evenements", []):
        try:
            valides.append(EvenementExtrait.model_validate(item))
        except ValidationError as exc:
            log.debug("Événement Gemini ignoré (format) : %s", exc)
    return ExtractionResultat(evenements=valides)


def extraire_evenements(docs: list[RawDocument], aujourdhui: date, modele: str) -> list[EvenementExtrait]:
    from google.genai import types

    client = _client()
    systeme = CONSIGNE_EXTRACTION.format(aujourdhui=aujourdhui.isoformat()) + FORMAT_JSON
    resultats: list[EvenementExtrait] = []
    for i, lot in enumerate(_lots(docs), 1):
        if i > 1:
            time.sleep(PAUSE_ENTRE_APPELS)
        try:
            reponse = _appeler(
                client,
                model=modele,
                contents=lot,
                config=types.GenerateContentConfig(
                    system_instruction=systeme,
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
        except IAIndisponible:
            if not resultats:
                raise
            log.error("Extraction Gemini lot %d ignoré", i)
            continue
        lu = _lire_json(reponse.text or "")
        if lu is None:
            log.warning("Extraction Gemini lot %d : réponse illisible", i)
            continue
        resultats.extend(lu.evenements)
        log.info("Extraction Gemini lot %d : %d événements", i, len(lu.evenements))
    return resultats
