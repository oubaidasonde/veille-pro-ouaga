"""Vérification et classement : confirmé / à vérifier / passé."""

from __future__ import annotations

import logging
from datetime import date

import httpx

from .config import Config
from .models import Evenement

log = logging.getLogger(__name__)

SEUIL_CONFIRME = 70


def verifier_lien(url: str, http: httpx.Client) -> bool | None:
    """True si le lien répond, False s'il est mort, None si la vérification est impossible."""
    if not url or not url.startswith("http"):
        return None
    try:
        resp = http.head(url)
        if resp.status_code in (403, 405, 501):  # certains sites refusent HEAD
            resp = http.get(url)
        return resp.status_code < 400
    except httpx.HTTPError:
        return False


def evaluer(ev: Evenement, cfg: Config, aujourdhui: date) -> Evenement:
    """Calcule le score de confiance, les points à vérifier et le statut."""
    points: list[str] = []
    score = 0
    nb_sources = len({u for u in ev.source_urls if u})

    lieu = cfg.lieu_connu(ev.lieu) or cfg.lieu_connu(ev.titre)
    ev.lieu_surveille = lieu.nom if lieu else None

    if ev.debut:
        score += 25
    else:
        points.append("Date non publiée")
    if ev.lieu:
        score += 15 + (10 if lieu else 0)
    else:
        points.append("Lieu exact non précisé")
    if ev.organisateur:
        score += 10
    else:
        points.append("Organisateur à identifier")
    if ev.gratuit or ev.tarif_fcfa is not None:
        score += 10
    else:
        points.append("Tarif à vérifier")
    if ev.inscription_url or ev.contact:
        score += 10
    else:
        points.append("Lien ou contact d'inscription à rechercher")
    if ev.source_officielle:
        score += 15
    if nb_sources >= 2:
        score += 15
    if not ev.source_officielle and nb_sources < 2:
        points.append("Source unique non officielle : confirmer auprès de l'organisateur")
    if ev.lien_inscription_ok is True:
        score += 5
    elif ev.lien_inscription_ok is False:
        points.append("Le lien d'inscription ne répond pas")
    if ev.cloture and ev.cloture < aujourdhui:
        points.append("Date limite d'inscription dépassée")

    ev.score_confiance = min(score, 100)
    ev.points_a_verifier = points

    titre = ev.titre.lower()
    if "annul" in titre or "report" in titre:
        ev.statut = "annule"
    elif ev.fin and ev.fin < aujourdhui:
        ev.statut = "passe"
    elif (ev.score_confiance >= SEUIL_CONFIRME and ev.debut and ev.lieu
          and (ev.source_officielle or nb_sources >= 2)):
        ev.statut = "confirme"
    else:
        ev.statut = "a_verifier"
    return ev


def verifier_liens(evenements: list[Evenement], http: httpx.Client) -> None:
    for ev in evenements:
        if ev.inscription_url and ev.lien_inscription_ok is None:
            ev.lien_inscription_ok = verifier_lien(ev.inscription_url, http)
