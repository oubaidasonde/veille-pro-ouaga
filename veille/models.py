"""Modèles de données : documents bruts collectés et événements structurés."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

TypeEvenement = Literal[
    "formation", "conference", "seminaire", "atelier", "forum", "salon", "rencontre", "autre"
]
Theme = Literal[
    "entrepreneuriat", "finance", "numerique", "marketing", "management",
    "marches_publics", "developpement_personnel", "diplomatie", "autre",
]
Statut = Literal["confirme", "a_verifier", "annule", "passe"]


class RawDocument(BaseModel):
    """Contenu brut récupéré par un collecteur (article, page web, synthèse de recherche)."""

    source: str                      # "actualites", "page", "recherche_ia"
    url: str
    titre: str = ""
    texte: str = ""
    publie_le: Optional[str] = None  # ISO 8601 si connu


class EvenementExtrait(BaseModel):
    """Schéma demandé au modèle lors de l'extraction (sorties structurées)."""

    titre: str
    type: TypeEvenement
    themes: list[Theme]
    resume: str = Field(description="Une à deux phrases, en français.")
    date_debut: Optional[str] = Field(None, description="AAAA-MM-JJ si connue, sinon null.")
    date_fin: Optional[str] = Field(None, description="AAAA-MM-JJ si connue, sinon null.")
    horaires: Optional[str] = None
    lieu: Optional[str] = Field(None, description="Hôtel / salle tels qu'annoncés.")
    ville: Optional[str] = None
    organisateur: Optional[str] = None
    public_cible: Optional[str] = None
    tarif: Optional[str] = Field(None, description="Tarif tel qu'annoncé, ex. '75 000 FCFA'.")
    tarif_fcfa: Optional[int] = Field(None, description="Montant principal en FCFA, 0 si gratuit.")
    gratuit: Optional[bool] = None
    inscription_url: Optional[str] = None
    date_limite_inscription: Optional[str] = Field(None, description="AAAA-MM-JJ si connue.")
    contact: Optional[str] = Field(None, description="Téléphone, e-mail ou WhatsApp publiés.")
    source_officielle: bool = Field(
        description="True si la source est l'organisateur, le lieu ou une plateforme d'inscription."
    )
    source_urls: list[str]


class ExtractionResultat(BaseModel):
    evenements: list[EvenementExtrait]


class Evenement(EvenementExtrait):
    """Événement consolidé, vérifié et stocké."""

    id: str = ""
    statut: Statut = "a_verifier"
    score_confiance: int = 0              # 0 – 100
    points_a_verifier: list[str] = Field(default_factory=list)
    lieu_surveille: Optional[str] = None  # nom canonique si le lieu fait partie de la liste suivie
    lien_inscription_ok: Optional[bool] = None
    premiere_detection: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    derniere_mise_a_jour: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    envoye_le: list[str] = Field(default_factory=list)  # identifiants de bulletins

    def calculer_id(self) -> str:
        cle = f"{normaliser(self.titre)[:60]}|{self.date_debut or ''}|{normaliser(self.ville or '')}"
        self.id = hashlib.sha1(cle.encode()).hexdigest()[:12]
        return self.id

    @property
    def debut(self) -> Optional[date]:
        return parse_iso(self.date_debut)

    @property
    def fin(self) -> Optional[date]:
        return parse_iso(self.date_fin) or self.debut

    @property
    def cloture(self) -> Optional[date]:
        return parse_iso(self.date_limite_inscription)


def parse_iso(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def normaliser(texte: str) -> str:
    """Minuscule, sans accents ni ponctuation : sert au dédoublonnage."""
    t = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()
