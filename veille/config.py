"""Chargement et validation de la configuration (config/config.yaml + variables d'environnement)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "config.yaml"

THEMES: dict[str, str] = {
    "entrepreneuriat": "Entrepreneuriat et business",
    "finance": "Finance et investissement",
    "numerique": "Technologies et numérique",
    "marketing": "Marketing et communication",
    "management": "Management et leadership",
    "marches_publics": "Marchés publics et passation des marchés",
    "developpement_personnel": "Développement personnel",
    "diplomatie": "Diplomatie et conférences internationales",
    "autre": "Autre",
}


class Profil(BaseModel):
    nom: str = "Veille professionnelle — Ouagadougou"
    langue: str = "fr"
    fuseau_horaire: str = "Africa/Ouagadougou"


class Thematiques(BaseModel):
    toutes: bool = True
    selection: list[str] = Field(default_factory=lambda: list(THEMES))

    def actives(self) -> set[str]:
        return set(THEMES) if self.toutes else set(self.selection)


class Budget(BaseModel):
    mode: Literal["gratuit", "petit_prix", "tous"] = "tous"
    plafond_fcfa: int = 50000


class Lieu(BaseModel):
    nom: str
    type: Literal["hotel", "centre_conference", "autre"] = "hotel"
    alias: list[str] = Field(default_factory=list)
    site: str = ""


class RechercheIA(BaseModel):
    active: bool = True
    fournisseur: Literal["gemini", "claude"] = "gemini"
    modele_gemini: str = "gemini-2.5-flash"
    modele: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_recherches: int = 12


class Config(BaseModel):
    profil: Profil = Field(default_factory=Profil)
    thematiques: Thematiques = Field(default_factory=Thematiques)
    budget: Budget = Field(default_factory=Budget)
    frequence: Literal["quotidien", "hebdo_et_mensuel", "mensuel"] = "hebdo_et_mensuel"
    canaux: list[Literal["email", "telegram", "whatsapp"]] = Field(default_factory=lambda: ["email"])
    whatsapp_fournisseur: Literal["twilio", "callmebot"] = "callmebot"
    zone: Literal["ouagadougou", "burkina"] = "burkina"
    horizon_jours: int = 120
    alerte_cloture_jours: int = 10
    lieux: list[Lieu] = Field(default_factory=list)
    organismes: list[str] = Field(default_factory=list)
    pages_suivies: list[str] = Field(default_factory=list)
    requetes_actualites: list[str] = Field(default_factory=list)
    recherche_ia: RechercheIA = Field(default_factory=RechercheIA)

    @property
    def ia_disponible(self) -> bool:
        if self.recherche_ia.fournisseur == "gemini":
            return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def lieu_connu(self, texte: str | None) -> Lieu | None:
        """Retrouve un lieu surveillé à partir d'un texte libre (nom d'hôtel, adresse...)."""
        if not texte:
            return None
        t = texte.lower()
        for lieu in self.lieux:
            if lieu.nom.lower() in t or any(a.lower() in t for a in lieu.alias):
                return lieu
        return None


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path or os.environ.get("VEILLE_CONFIG", DEFAULT_CONFIG))
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return Config.model_validate(data)
