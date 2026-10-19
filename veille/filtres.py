"""Application des préférences : thèmes, budget, zone, horizon."""

from __future__ import annotations

from datetime import date, timedelta

from .config import Config
from .models import Evenement


def dans_la_zone(ev: Evenement, cfg: Config) -> bool:
    if cfg.zone == "burkina":
        return True
    ville = (ev.ville or "").lower()
    return "ouaga" in ville or ev.lieu_surveille is not None or not ville


def dans_le_budget(ev: Evenement, cfg: Config) -> bool:
    if cfg.budget.mode == "tous" or (ev.tarif_fcfa is None and not ev.gratuit):
        return True  # tarif non publié : conservé, signalé « à vérifier »
    gratuit = ev.gratuit or ev.tarif_fcfa == 0
    if cfg.budget.mode == "gratuit":
        return bool(gratuit)
    return bool(gratuit) or (ev.tarif_fcfa or 0) <= cfg.budget.plafond_fcfa


def dans_les_themes(ev: Evenement, cfg: Config) -> bool:
    return cfg.thematiques.toutes or bool(set(ev.themes) & cfg.thematiques.actives())


def dans_l_horizon(ev: Evenement, cfg: Config, aujourdhui: date) -> bool:
    if ev.statut in ("passe", "annule"):
        return False
    if ev.debut is None:
        return True
    return ev.debut <= aujourdhui + timedelta(days=cfg.horizon_jours)


def selectionner(evenements: list[Evenement], cfg: Config, aujourdhui: date) -> list[Evenement]:
    retenus = [
        ev for ev in evenements
        if dans_l_horizon(ev, cfg, aujourdhui) and dans_la_zone(ev, cfg)
        and dans_le_budget(ev, cfg) and dans_les_themes(ev, cfg)
    ]
    return sorted(retenus, key=lambda e: cle_tri(e, cfg))


def cle_tri(ev: Evenement, cfg: Config) -> tuple:
    """Confirmés d'abord, puis par date ; Ouagadougou avant le reste du pays."""
    hors_ouaga = cfg.zone == "burkina" and "ouaga" not in (ev.ville or "ouaga").lower()
    return (ev.statut != "confirme", hors_ouaga, ev.date_debut or "9999-12-31", -ev.score_confiance)
