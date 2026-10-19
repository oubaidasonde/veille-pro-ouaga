"""Mise en forme du bulletin : HTML (e-mail), Markdown (archive) et texte court (messageries)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT, THEMES, Config
from .models import Evenement

MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"]
JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
TYPES_FR = {
    "formation": "Formation", "conference": "Conférence", "seminaire": "Séminaire",
    "atelier": "Atelier", "forum": "Forum", "salon": "Salon",
    "rencontre": "Rencontre professionnelle", "autre": "Événement",
}
STATUTS_FR = {"confirme": "Confirmé", "a_verifier": "À vérifier", "annule": "Annulé", "passe": "Passé"}
PERIODES_FR = {"quotidien": "Alerte du jour", "hebdo": "Bulletin hebdomadaire", "mensuel": "Bilan mensuel"}


def date_fr(d: date | None, avec_jour: bool = True) -> str:
    if d is None:
        return "à confirmer"
    jour = "1er" if d.day == 1 else str(d.day)
    texte = f"{jour} {MOIS_FR[d.month - 1]} {d.year}"
    return f"{JOURS_FR[d.weekday()]} {texte}" if avec_jour else texte


def periode_fr(ev: Evenement) -> str:
    if ev.debut is None:
        return "Date à confirmer"
    if ev.fin and ev.fin != ev.debut:
        if ev.fin.month == ev.debut.month and ev.fin.year == ev.debut.year:
            return f"Du {ev.debut.day} au {date_fr(ev.fin, False)}"
        return f"Du {date_fr(ev.debut, False)} au {date_fr(ev.fin, False)}"
    return date_fr(ev.debut).capitalize()


def ville_fr(ev: Evenement) -> str:
    """Suffixe « , Ville » sauf si la ville figure déjà dans le nom du lieu."""
    if not ev.ville or (ev.lieu and ev.ville.lower()[:5] in ev.lieu.lower()):
        return ""
    return f", {ev.ville}"


def tarif_fr(ev: Evenement) -> str:
    if ev.gratuit or ev.tarif_fcfa == 0:
        return "Gratuit"
    if ev.tarif:
        return ev.tarif
    if ev.tarif_fcfa:
        return f"{ev.tarif_fcfa:,} FCFA".replace(",", " ")
    return "Tarif à vérifier"


@dataclass
class Bulletin:
    periode: str                 # quotidien | hebdo | mensuel
    aujourdhui: date
    evenements: list[Evenement]
    nouveaux_ids: set[str] = field(default_factory=set)

    @property
    def identifiant(self) -> str:
        return f"{self.periode}-{self.aujourdhui.isoformat()}"

    @property
    def titre(self) -> str:
        if self.periode == "mensuel":
            return f"{PERIODES_FR['mensuel']} — {MOIS_FR[self.aujourdhui.month - 1]} {self.aujourdhui.year}"
        return f"{PERIODES_FR[self.periode]} — {date_fr(self.aujourdhui, False)}"

    @property
    def confirmes(self) -> list[Evenement]:
        return [e for e in self.evenements if e.statut == "confirme"]

    @property
    def a_verifier(self) -> list[Evenement]:
        return [e for e in self.evenements if e.statut == "a_verifier"]

    def clotures_proches(self, jours: int) -> list[Evenement]:
        limite = self.aujourdhui + timedelta(days=jours)
        return [e for e in self.evenements if e.cloture and self.aujourdhui <= e.cloture <= limite]


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(ROOT / "veille" / "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(date_fr=date_fr, periode_fr=periode_fr, tarif_fr=tarif_fr, ville_fr=ville_fr)
    env.globals.update(TYPES_FR=TYPES_FR, STATUTS_FR=STATUTS_FR, THEMES=THEMES)
    return env


def _contexte(b: Bulletin, cfg: Config) -> dict:
    return {
        "b": b,
        "cfg": cfg,
        "clotures": b.clotures_proches(cfg.alerte_cloture_jours),
        "zone": "Ouagadougou" if cfg.zone == "ouagadougou" else "Burkina Faso (priorité Ouagadougou)",
    }


def rendre_html(b: Bulletin, cfg: Config) -> str:
    return _env().get_template("bulletin.html.j2").render(**_contexte(b, cfg))


def rendre_markdown(b: Bulletin, cfg: Config) -> str:
    return _env().get_template("bulletin.md.j2").render(**_contexte(b, cfg))


def rendre_texte_court(b: Bulletin, cfg: Config, limite: int = 3800) -> list[str]:
    """Texte brut découpé en messages (Telegram : 4096 caractères max, WhatsApp similaire)."""
    lignes = [f"📌 {cfg.profil.nom}", b.titre, ""]
    if not b.evenements:
        lignes.append("Aucun nouvel événement correspondant à vos critères pour cette période.")
    clotures = b.clotures_proches(cfg.alerte_cloture_jours)
    if clotures:
        lignes.append("⏰ Inscriptions qui ferment bientôt :")
        lignes += [f"• {e.titre} — avant le {date_fr(e.cloture, False)}" for e in clotures]
        lignes.append("")
    for i, e in enumerate(b.evenements, 1):
        badge = "✅" if e.statut == "confirme" else "🔎"
        nouveau = " 🆕" if e.id in b.nouveaux_ids else ""
        bloc = [
            f"{badge} {i}. {TYPES_FR.get(e.type, 'Événement')} — {e.titre}{nouveau}",
            f"📅 {periode_fr(e)}" + (f" · {e.horaires}" if e.horaires else ""),
            f"📍 {e.lieu or 'Lieu à confirmer'}{ville_fr(e)}",
            f"💰 {tarif_fr(e)}",
        ]
        if e.organisateur:
            bloc.append(f"🏢 {e.organisateur}")
        if e.inscription_url:
            bloc.append(f"🔗 {e.inscription_url}")
        elif e.contact:
            bloc.append(f"☎️ {e.contact}")
        if e.statut != "confirme" and e.points_a_verifier:
            bloc.append(f"⚠️ {', '.join(e.points_a_verifier[:2])}")
        lignes += bloc + [""]
    lignes.append(f"✅ confirmé · 🔎 à vérifier — {len(b.confirmes)} confirmé(s), {len(b.a_verifier)} à vérifier")

    messages, courant = [], ""
    for ligne in lignes:
        if len(courant) + len(ligne) + 1 > limite:
            messages.append(courant.rstrip())
            courant = ""
        courant += ligne + "\n"
    if courant.strip():
        messages.append(courant.rstrip())
    return messages
