"""Génère docs/exemple-bulletin.{md,html} à partir d'événements FICTIFS (démonstration du format)."""

from datetime import date
from pathlib import Path

from veille.config import load_config
from veille.models import EvenementExtrait
from veille.pipeline import construire_bulletin
from veille.rapport import rendre_html, rendre_markdown, rendre_texte_court
from veille.store import Store
from veille.verification import evaluer

AUJ = date(2026, 11, 1)
FICTIFS = [
    dict(titre="[Exemple] Séminaire : la fiscalité des salaires", type="seminaire", themes=["finance"],
         resume="Exemple fictif — séminaire pratique sur l'IUTS, les cotisations sociales et les déclarations.",
         date_debut="2026-11-20", date_fin="2026-11-21", horaires="08h30 – 16h30",
         lieu="Azalaï Hôtel Ouagadougou", ville="Ouagadougou", organisateur="Cabinet de conseil (exemple)",
         public_cible="DRH, comptables, dirigeants de PME", tarif="150 000 FCFA (pause-café et déjeuner inclus)",
         tarif_fcfa=150000, gratuit=False, inscription_url="https://exemple.org/inscription",
         date_limite_inscription="2026-11-08", contact="+226 00 00 00 00", source_officielle=True,
         source_urls=["https://exemple.org/agenda", "https://exemple.org/post"]),
    dict(titre="[Exemple] Conférence professionnelle sur le numérique", type="conference", themes=["numerique"],
         resume="Exemple fictif — annonce repérée sur un réseau social, détails à confirmer.",
         date_debut=None, date_fin=None, lieu="Hôtel à Ouagadougou, à confirmer", ville="Ouagadougou",
         organisateur=None, tarif=None, tarif_fcfa=None, gratuit=None, inscription_url=None,
         source_officielle=False, source_urls=["https://exemple.org/annonce"]),
    dict(titre="[Exemple] Atelier passation des marchés publics", type="atelier", themes=["marches_publics"],
         resume="Exemple fictif — atelier gratuit ouvert aux PME soumissionnaires.",
         date_debut="2026-12-03", date_fin="2026-12-03", lieu="Lancaster Ouaga 2000", ville="Ouagadougou",
         organisateur="Organisme public (exemple)", public_cible="PME, entrepreneurs", tarif="Gratuit",
         tarif_fcfa=0, gratuit=True, inscription_url="https://exemple.org/atelier",
         source_officielle=True, source_urls=["https://exemple.org/atelier"]),
]


def main() -> None:
    import tempfile

    cfg = load_config()
    store = Store(Path(tempfile.mkdtemp()) / "exemple.json")
    for f in FICTIFS:
        ev, _ = store.integrer(EvenementExtrait(**f))
        evaluer(ev, cfg, AUJ)
    b = construire_bulletin(cfg, store, "mensuel", AUJ)
    sortie = Path(__file__).parent
    (sortie / "exemple-bulletin.md").write_text(rendre_markdown(b, cfg), encoding="utf-8")
    (sortie / "exemple-bulletin.html").write_text(rendre_html(b, cfg), encoding="utf-8")
    (sortie / "exemple-message.txt").write_text("\n\n".join(rendre_texte_court(b, cfg)), encoding="utf-8")


if __name__ == "__main__":
    main()
