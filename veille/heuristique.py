"""Extraction de secours, sans IA : mots-clés + reconnaissance des dates en français.

Utilisée quand aucune clé ANTHROPIC_API_KEY n'est configurée. Les événements
produits ainsi sont toujours classés « à vérifier ».
"""

from __future__ import annotations

import re
from datetime import date

from .config import Config
from .models import EvenementExtrait, RawDocument

MOIS = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12, "décembre": 12,
}
_MOIS_RE = "|".join(sorted(MOIS, key=len, reverse=True))

# « du 12 au 14 novembre 2026 », « 12-14 novembre », « les 3 et 4 décembre 2026 »
RE_PLAGE = re.compile(
    rf"\b(\d{{1,2}})(?:er)?\s*(?:au|-|–|et)\s*(\d{{1,2}})(?:er)?\s+({_MOIS_RE})(?:\s+(\d{{4}}))?",
    re.IGNORECASE,
)
# « le 30 mai 2026 », « 1er décembre »
RE_JOUR = re.compile(rf"\b(\d{{1,2}})(?:er)?\s+({_MOIS_RE})(?:\s+(\d{{4}}))?", re.IGNORECASE)
# « 12/11/2026 »
RE_NUM = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d{2})\b")

TYPES = {
    "formation": "formation", "séminaire": "seminaire", "seminaire": "seminaire",
    "atelier": "atelier", "conférence": "conference", "conference": "conference",
    "forum": "forum", "salon": "salon", "panel": "conference", "masterclass": "formation",
    "webinaire": "formation", "rencontre": "rencontre", "colloque": "conference",
    "symposium": "conference", "journées": "forum", "hackathon": "atelier",
}
PASSE = ("retour en images", "a organisé", "s'est tenu", "s’est tenu", "a pris fin", "ont pris fin",
         "a clôturé", "ont clôturé", "clôture des travaux", "s'est achevé")
THEMES_MOTS = {
    "entrepreneuriat": ("entrepreneur", "startup", "start-up", "pme", "business", "incubat"),
    "finance": ("financ", "fiscal", "comptab", "banque", "investiss", "bourse", "brvm", "ohada"),
    "numerique": ("numérique", "digital", "intelligence artificielle", " ia ", "cyber", "tic", "data"),
    "marketing": ("marketing", "communication", "vente", "commercial", "marque"),
    "management": ("management", "leadership", "gestion de projet", "ressources humaines", "rh "),
    "marches_publics": ("marchés publics", "passation", "commande publique", "arcop", "appel d'offres"),
    "developpement_personnel": ("développement personnel", "coaching", "prise de parole", "soft skills"),
    "diplomatie": ("diplomat", "ambassade", "international", "coopération", "sommet"),
}
RE_PRIX = re.compile(r"(\d{1,3}(?:[ . ]\d{3})+|\d{4,7})\s*(?:f\s?cfa|fcfa|xof|francs?\s+cfa)", re.I)
RE_URL = re.compile(r"https?://[^\s<>\"')]+")
RE_TEL = re.compile(r"(?:\+226[\s.]?)?(?:[025-7]\d[\s.]?){4}\b")


def _an(annee: str | None, mois: int, jour: int, ref: date) -> date | None:
    try:
        if annee:
            return date(int(annee), mois, jour)
        d = date(ref.year, mois, jour)
        return d if d >= ref else date(ref.year + 1, mois, jour)
    except ValueError:
        return None


def trouver_dates(texte: str, ref: date) -> tuple[date | None, date | None]:
    if m := RE_PLAGE.search(texte):
        mois = MOIS[m.group(3).lower()]
        d1, d2 = _an(m.group(4), mois, int(m.group(1)), ref), _an(m.group(4), mois, int(m.group(2)), ref)
        return d1, d2
    if m := RE_JOUR.search(texte):
        d = _an(m.group(3), MOIS[m.group(2).lower()], int(m.group(1)), ref)
        return d, d
    if m := RE_NUM.search(texte):
        try:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return d, d
        except ValueError:
            pass
    return None, None


def detecter_type(texte: str) -> str | None:
    t = texte.lower()
    for mot, typ in TYPES.items():
        if mot in t:
            return typ
    return None


def detecter_themes(texte: str) -> list[str]:
    t = f" {texte.lower()} "
    themes = [th for th, mots in THEMES_MOTS.items() if any(m in t for m in mots)]
    return themes or ["autre"]


def extraire_evenements_heuristique(docs: list[RawDocument], cfg: Config,
                                    aujourdhui: date) -> list[EvenementExtrait]:
    evenements = []
    for doc in docs:
        texte = f"{doc.titre}. {doc.texte}"
        bas = texte.lower()
        typ = detecter_type(texte)
        if not typ or any(p in bas for p in PASSE):
            continue
        if "burkina" not in bas and "ouaga" not in bas and not cfg.lieu_connu(texte):
            continue
        debut, fin = trouver_dates(texte, aujourdhui)
        if debut and (fin or debut) < aujourdhui:
            continue
        lieu = cfg.lieu_connu(texte)
        prix = RE_PRIX.search(texte)
        montant = int(re.sub(r"\D", "", prix.group(1))) if prix else None
        gratuit = True if re.search(r"\b(gratuit|entrée libre|accès libre)\b", bas) else None
        urls = [u for u in RE_URL.findall(doc.texte) if "news.google" not in u]
        tel = RE_TEL.search(doc.texte)
        evenements.append(EvenementExtrait(
            titre=(doc.titre or texte[:120]).split(" - ")[0].strip(),
            type=typ,
            themes=detecter_themes(texte),
            resume=doc.texte[:280] or doc.titre,
            date_debut=debut.isoformat() if debut else None,
            date_fin=fin.isoformat() if fin else None,
            lieu=lieu.nom if lieu else None,
            ville="Ouagadougou" if "ouaga" in bas or lieu else None,
            tarif=prix.group(0) if prix else ("Gratuit" if gratuit else None),
            tarif_fcfa=0 if gratuit else montant,
            gratuit=gratuit,
            inscription_url=urls[0] if urls else None,
            contact=tel.group(0).strip() if tel else None,
            source_officielle=doc.source == "page",
            source_urls=[doc.url],
        ))
    return evenements
