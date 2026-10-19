"""Orchestration : collecte → extraction → vérification → stockage → bulletin → envoi."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import ROOT, Config
from .filtres import selectionner
from .heuristique import extraire_evenements_heuristique
from .llm import IAIndisponible, extraire_evenements_ia
from .models import RawDocument
from .rapport import Bulletin, rendre_html, rendre_markdown
from .sources import collecter_tout, nouveau_client_http
from .store import Store
from .verification import evaluer, verifier_liens

log = logging.getLogger(__name__)
DOSSIER_RAPPORTS = ROOT / "reports"


@dataclass
class BilanCollecte:
    documents: int
    extraits: int
    nouveaux: int
    mis_a_jour: int
    total: int


def traiter_documents(docs: list[RawDocument], cfg: Config, store: Store, aujourdhui: date,
                      verifier_les_liens: bool = True) -> BilanCollecte:
    extraits = None
    if cfg.ia_disponible and docs:
        try:
            if cfg.recherche_ia.fournisseur == "gemini":
                from .gemini import extraire_evenements as extraire_gemini
                extraits = extraire_gemini(docs, aujourdhui, cfg.recherche_ia.modele_gemini)
            else:
                extraits = extraire_evenements_ia(docs, aujourdhui, cfg.recherche_ia.modele,
                                                  cfg.recherche_ia.effort)
        except IAIndisponible as exc:
            log.warning("IA indisponible (%s) : extraction par mots-clés à la place.", exc)
    elif not cfg.ia_disponible:
        log.warning("Pas de clé d'IA (GEMINI_API_KEY ou ANTHROPIC_API_KEY) : extraction par mots-clés (résultats « à vérifier »).")
    if extraits is None:
        extraits = extraire_evenements_heuristique(docs, cfg, aujourdhui)

    nouveaux = mis_a_jour = 0
    touches = []
    for ex in extraits:
        ev, est_nouveau = store.integrer(ex)
        touches.append(ev)
        nouveaux += est_nouveau
        mis_a_jour += not est_nouveau

    if verifier_les_liens:
        with nouveau_client_http() as http:
            verifier_liens(touches, http)
    for ev in store.tous():  # réévalue tout : un événement peut devenir « passé »
        evaluer(ev, cfg, aujourdhui)
    store.sauvegarder()
    return BilanCollecte(len(docs), len(extraits), nouveaux, mis_a_jour, len(store.evenements))


def collecter(cfg: Config, store: Store, aujourdhui: date) -> BilanCollecte:
    docs = collecter_tout(cfg, aujourdhui)
    return traiter_documents(docs, cfg, store, aujourdhui)


def construire_bulletin(cfg: Config, store: Store, periode: str, aujourdhui: date) -> Bulletin:
    """
    - quotidien : uniquement les événements jamais envoyés ;
    - hebdo     : événements pas encore présentés dans un bulletin hebdo + clôtures proches ;
    - mensuel   : récapitulatif complet de tout ce qui est à venir.
    """
    candidats = selectionner(store.tous(), cfg, aujourdhui)
    jamais_envoyes = {e.id for e in candidats if not e.envoye_le}

    if periode == "quotidien":
        retenus = [e for e in candidats if e.id in jamais_envoyes]
    elif periode == "hebdo":
        b_tmp = Bulletin(periode, aujourdhui, candidats)
        proches = {e.id for e in b_tmp.clotures_proches(cfg.alerte_cloture_jours)}
        retenus = [e for e in candidats
                   if e.id in proches or not any(x.startswith("hebdo-") for x in e.envoye_le)]
    else:
        retenus = candidats
    return Bulletin(periode, aujourdhui, retenus, nouveaux_ids=jamais_envoyes)


def archiver(b: Bulletin, cfg: Config, dossier: Path = DOSSIER_RAPPORTS) -> tuple[Path, Path]:
    dossier.mkdir(parents=True, exist_ok=True)
    md = dossier / f"{b.identifiant}.md"
    html = dossier / f"{b.identifiant}.html"
    md.write_text(rendre_markdown(b, cfg), encoding="utf-8")
    html.write_text(rendre_html(b, cfg), encoding="utf-8")
    return md, html


def marquer_envoye(b: Bulletin, store: Store) -> None:
    for e in b.evenements:
        if b.identifiant not in e.envoye_le:
            e.envoye_le.append(b.identifiant)
    store.sauvegarder()


def periodes_du_jour(cfg: Config, aujourdhui: date) -> list[str]:
    """Bulletins à produire aujourd'hui selon la fréquence choisie."""
    periodes = []
    if cfg.frequence == "quotidien":
        periodes.append("quotidien")
    if cfg.frequence == "hebdo_et_mensuel" and aujourdhui.weekday() == 0:  # lundi
        periodes.append("hebdo")
    if cfg.frequence in ("hebdo_et_mensuel", "mensuel", "quotidien") and aujourdhui.day == 1:
        periodes.append("mensuel")
    return periodes
