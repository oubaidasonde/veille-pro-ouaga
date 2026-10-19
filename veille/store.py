"""Base d'événements persistante (fichier JSON versionné) avec dédoublonnage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from .config import ROOT
from .models import Evenement, EvenementExtrait, normaliser

DEFAULT_STORE = ROOT / "data" / "evenements.json"
SIMILARITE_TITRE = 0.82


class Store:
    def __init__(self, path: str | Path = DEFAULT_STORE):
        self.path = Path(path)
        self.evenements: dict[str, Evenement] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
            for item in data:
                ev = Evenement.model_validate(item)
                self.evenements[ev.id] = ev

    def sauvegarder(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        items = sorted(self.evenements.values(), key=lambda e: (e.date_debut or "9999", e.titre))
        self.path.write_text(
            json.dumps([e.model_dump() for e in items], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _doublon(self, ev: Evenement) -> Evenement | None:
        if ev.id in self.evenements:
            return self.evenements[ev.id]
        titre = normaliser(ev.titre)
        for autre in self.evenements.values():
            if ev.date_debut and autre.date_debut and ev.date_debut != autre.date_debut:
                continue
            if SequenceMatcher(None, titre, normaliser(autre.titre)).ratio() >= SIMILARITE_TITRE:
                return autre
        return None

    def integrer(self, extrait: EvenementExtrait) -> tuple[Evenement, bool]:
        """Ajoute ou fusionne un événement. Renvoie (événement, est_nouveau)."""
        ev = Evenement.model_validate(extrait.model_dump())
        ev.calculer_id()
        existant = self._doublon(ev)
        if existant is None:
            self.evenements[ev.id] = ev
            return ev, True

        for champ, valeur in extrait.model_dump().items():
            if champ == "source_urls":
                existant.source_urls = list(dict.fromkeys(existant.source_urls + valeur))
            elif champ == "themes":
                existant.themes = list(dict.fromkeys(existant.themes + valeur))
            elif champ == "source_officielle":
                existant.source_officielle = existant.source_officielle or valeur
            elif valeur not in (None, "", []) and getattr(existant, champ) in (None, "", []):
                setattr(existant, champ, valeur)
        existant.derniere_mise_a_jour = datetime.now(timezone.utc).isoformat()
        return existant, False

    def tous(self) -> list[Evenement]:
        return list(self.evenements.values())
