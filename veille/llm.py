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

À partir des documents fournis, extrais les OPPORTUNITÉS PROFESSIONNELLES à venir qui
concernent un professionnel basé au Burkina Faso :
1. les événements (formation, conférence, séminaire, atelier, forum, salon, rencontre
   professionnelle) qui se tiennent au Burkina Faso ;
2. les appels à candidatures, manifestations d'intérêt ou inscriptions ouvertes pour une
   FORMATION professionnelle (y compris gratuite ou en ligne) accessible depuis le Burkina Faso.
Conserve-les si leur date (ou leur date limite d'inscription) est postérieure ou égale à la
date du jour, ou si elle est inconnue mais que l'annonce est manifestement récente et ouverte.

Règles :
- Thèmes professionnels : entrepreneuriat, finance, numérique, marketing, management,
  marchés publics, développement personnel, diplomatie, hôtellerie, agriculture, etc.
- Exclure : événements culturels, artistiques, musicaux, sportifs, religieux, festifs ou
  politiques ; concours de la fonction publique, recrutements d'emploi, bourses d'études,
  admissions universitaires (licence, master, écoles), résultats d'examens, remises de dons
  ou de diplômes ; comptes rendus d'événements passés (« a organisé », « s'est tenu »,
  « clôture ») ; appels d'offres sans événement ; publicité hôtelière.
- Beaucoup de documents ne contiennent qu'un TITRE : si le titre suffit à identifier une
  opportunité à venir, extrais-la quand même avec les champs inconnus à null.
- Formation en ligne : ville = "En ligne". À l'étranger mais ouverte aux Burkinabè : ville = la
  ville étrangère.
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
    """L'IA ne répond pas (crédit épuisé, clé invalide, service inaccessible)."""


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
