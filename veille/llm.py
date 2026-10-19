"""Accès à Claude : client partagé et extraction structurée des événements."""

from __future__ import annotations

import logging
from datetime import date

import anthropic

from .models import ExtractionResultat, EvenementExtrait, RawDocument

log = logging.getLogger(__name__)

TAILLE_LOT = 40_000  # caractères de documents par appel d'extraction

CONSIGNE_EXTRACTION = """Tu es analyste de veille événementielle pour un professionnel basé à Ouagadougou.
Date du jour : {aujourdhui}.

À partir des documents fournis, extrais UNIQUEMENT les événements professionnels
(formation, conférence, séminaire, atelier, forum, salon, rencontre) qui se tiennent
au Burkina Faso et dont la date est postérieure ou égale à la date du jour, ou inconnue
mais manifestement à venir.

Règles strictes :
- Uniquement des événements PROFESSIONNELS : formations, renforcement de capacités, séminaires
  et ateliers techniques, conférences économiques, financières, numériques, institutionnelles
  ou diplomatiques, forums d'affaires, salons professionnels, rencontres B2B.
- Exclure les événements culturels, artistiques, musicaux, sportifs, religieux, festifs ou
  politiques (festivals, concerts, rencontres musicales, expositions d'art, symposiums de
  sculpture, compétitions, cérémonies, meetings), ainsi que les remises de dons ou de diplômes.
- Exclure les comptes rendus d'événements passés (« retour en images », « a organisé »,
  « s'est tenu »), les offres d'emploi, les appels d'offres sans événement, la publicité hôtelière.
- Ne rien inventer : un champ absent du document vaut null. Pas de date déduite au hasard.
- Dates au format AAAA-MM-JJ. Si seule l'année manque, prends la prochaine occurrence.
- tarif_fcfa : montant principal en FCFA (0 si gratuit), null si non publié.
- source_officielle = true seulement si le document émane de l'organisateur, du lieu
  ou d'une plateforme d'inscription.
- source_urls : les URL des documents d'où vient l'information.
- Fusionne les doublons (même événement vu dans plusieurs documents) en un seul objet,
  en conservant toutes les URL sources.
- themes parmi : entrepreneuriat, finance, numerique, marketing, management,
  marches_publics, developpement_personnel, diplomatie, autre.
S'il n'y a aucun événement, renvoie une liste vide."""


class IAIndisponible(RuntimeError):
    """Claude ne répond pas (crédit épuisé, clé invalide, service inaccessible)."""


def client_ia() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=4)


def texte_reponse(reponse) -> str:
    return "\n".join(b.text for b in reponse.content if b.type == "text").strip()


def _lots(docs: list[RawDocument]) -> list[str]:
    lots, courant = [], ""
    for d in docs:
        bloc = f"<document url=\"{d.url}\" source=\"{d.source}\" publie=\"{d.publie_le or ''}\">\n" \
               f"<titre>{d.titre}</titre>\n{d.texte}\n</document>\n"
        if courant and len(courant) + len(bloc) > TAILLE_LOT:
            lots.append(courant)
            courant = ""
        courant += bloc
    if courant:
        lots.append(courant)
    return lots


def extraire_evenements_ia(docs: list[RawDocument], aujourdhui: date,
                           modele: str, effort: str) -> list[EvenementExtrait]:
    client = client_ia()
    systeme = CONSIGNE_EXTRACTION.format(aujourdhui=aujourdhui.isoformat())
    resultats: list[EvenementExtrait] = []
    for i, lot in enumerate(_lots(docs), 1):
        try:
            reponse = client.messages.parse(
                model=modele,
                max_tokens=16000,
                system=systeme,
                output_config={"effort": effort},
                output_format=ExtractionResultat,
                messages=[{"role": "user", "content": lot}],
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            if not resultats:  # échec dès le départ : inutile d'insister sur les autres lots
                raise IAIndisponible(str(exc)) from exc
            log.error("Extraction lot %d ignoré : %s", i, exc)
            continue
        if reponse.stop_reason in ("refusal", "max_tokens") or reponse.parsed_output is None:
            log.warning("Extraction lot %d ignoré (stop_reason=%s)", i, reponse.stop_reason)
            continue
        resultats.extend(reponse.parsed_output.evenements)
        log.info("Extraction lot %d : %d événements", i, len(reponse.parsed_output.evenements))
    return resultats
