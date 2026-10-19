from datetime import date

import pytest

from veille.config import load_config
from veille.filtres import selectionner
from veille.heuristique import extraire_evenements_heuristique, trouver_dates
from veille.models import EvenementExtrait, RawDocument
from veille.pipeline import construire_bulletin, marquer_envoye, periodes_du_jour, traiter_documents
from veille.rapport import rendre_html, rendre_markdown, rendre_texte_court
from veille.store import Store
from veille.verification import evaluer

AUJ = date(2026, 10, 19)


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ev.json")


def extrait(**kw) -> EvenementExtrait:
    base = dict(
        titre="Séminaire sur la fiscalité des salaires", type="seminaire", themes=["finance"],
        resume="Séminaire pratique.", date_debut="2026-11-12", date_fin="2026-11-13",
        lieu="Hôtel Azalaï Ouagadougou", ville="Ouagadougou", organisateur="Cabinet X",
        tarif="150 000 FCFA", tarif_fcfa=150000, gratuit=False,
        inscription_url="https://exemple.bf/inscription", source_officielle=True,
        source_urls=["https://exemple.bf/agenda"],
    )
    base.update(kw)
    return EvenementExtrait(**base)


def test_dates_francaises():
    assert trouver_dates("du 12 au 14 novembre 2026", AUJ) == (date(2026, 11, 12), date(2026, 11, 14))
    assert trouver_dates("le 1er décembre", AUJ) == (date(2026, 12, 1), date(2026, 12, 1))
    assert trouver_dates("le 3 mars", AUJ) == (date(2027, 3, 3), date(2027, 3, 3))  # prochaine occurrence
    assert trouver_dates("rendez-vous le 05/01/2027", AUJ) == (date(2027, 1, 5), date(2027, 1, 5))
    assert trouver_dates("aucune date", AUJ) == (None, None)


def test_heuristique_ignore_les_retours_en_images(cfg):
    docs = [
        RawDocument(source="actualites", url="https://a", titre="Retour en images : séminaire à Ouagadougou",
                    texte="Le cabinet a organisé un séminaire le 30 mai 2025."),
        RawDocument(source="actualites", url="https://b", titre="Atelier marchés publics à l'hôtel Silmandé",
                    texte="Atelier sur la passation des marchés du 24 au 26 novembre 2026 à Ouagadougou. "
                          "Frais : 75 000 FCFA. Inscription https://inscription.bf/atelier"),
    ]
    evs = extraire_evenements_heuristique(docs, cfg, AUJ)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.type == "atelier" and ev.lieu == "Sopatel Silmandé"
    assert ev.date_debut == "2026-11-24" and ev.tarif_fcfa == 75000
    assert "marches_publics" in ev.themes
    assert ev.inscription_url == "https://inscription.bf/atelier"


def test_classement_confirme_et_a_verifier(cfg, store):
    ev, _ = store.integrer(extrait())
    evaluer(ev, cfg, AUJ)
    assert ev.statut == "confirme" and ev.lieu_surveille == "Azalaï Hôtel Ouagadougou"

    flou, _ = store.integrer(extrait(titre="Forum numérique", type="forum", date_debut=None, date_fin=None,
                                     lieu=None, organisateur=None, tarif=None, tarif_fcfa=None,
                                     gratuit=None, inscription_url=None, source_officielle=False,
                                     source_urls=["https://facebook.com/x"]))
    evaluer(flou, cfg, AUJ)
    assert flou.statut == "a_verifier"
    assert "Date non publiée" in flou.points_a_verifier
    assert "Tarif à vérifier" in flou.points_a_verifier

    passe, _ = store.integrer(extrait(titre="Conférence ancienne", date_debut="2026-09-01", date_fin="2026-09-01"))
    evaluer(passe, cfg, AUJ)
    assert passe.statut == "passe"


def test_dedoublonnage_et_fusion(store):
    a, nouveau_a = store.integrer(extrait(contact=None))
    b, nouveau_b = store.integrer(extrait(titre="Séminaire : la fiscalité des salaires",
                                          source_urls=["https://facebook.com/post"], contact="+226 70 00 00 00"))
    assert nouveau_a and not nouveau_b and a is b
    assert len(store.evenements) == 1
    assert a.contact == "+226 70 00 00 00"
    assert set(a.source_urls) == {"https://exemple.bf/agenda", "https://facebook.com/post"}


def test_persistance(tmp_path, cfg):
    s = Store(tmp_path / "ev.json")
    ev, _ = s.integrer(extrait())
    evaluer(ev, cfg, AUJ)
    s.sauvegarder()
    relu = Store(tmp_path / "ev.json")
    assert relu.evenements[ev.id].statut == "confirme"


def test_filtres_budget_et_zone(cfg, store):
    for kw in [dict(), dict(titre="Atelier gratuit", tarif_fcfa=0, gratuit=True, tarif="Gratuit"),
               dict(titre="Forum Bobo", ville="Bobo-Dioulasso", lieu="Hôtel Bobo")]:
        ev, _ = store.integrer(extrait(**kw))
        evaluer(ev, cfg, AUJ)

    cfg.budget.mode = "gratuit"
    assert [e.titre for e in selectionner(store.tous(), cfg, AUJ)] == ["Atelier gratuit"]

    cfg.budget.mode = "tous"
    cfg.zone = "ouagadougou"
    assert "Forum Bobo" not in [e.titre for e in selectionner(store.tous(), cfg, AUJ)]
    cfg.zone = "burkina"
    titres = [e.titre for e in selectionner(store.tous(), cfg, AUJ)]
    assert titres[-1] == "Forum Bobo"  # hors Ouagadougou en fin de liste


def test_filtre_thematiques(cfg, store):
    ev, _ = store.integrer(extrait())
    evaluer(ev, cfg, AUJ)
    cfg.thematiques.toutes = False
    cfg.thematiques.selection = ["numerique"]
    assert selectionner(store.tous(), cfg, AUJ) == []
    cfg.thematiques.selection = ["finance"]
    assert len(selectionner(store.tous(), cfg, AUJ)) == 1


def test_bulletins_et_suivi_des_envois(cfg, store):
    ev, _ = store.integrer(extrait(date_limite_inscription="2026-10-25"))
    evaluer(ev, cfg, AUJ)

    quotidien = construire_bulletin(cfg, store, "quotidien", AUJ)
    assert len(quotidien.evenements) == 1
    marquer_envoye(quotidien, store)
    assert construire_bulletin(cfg, store, "quotidien", AUJ).evenements == []

    hebdo = construire_bulletin(cfg, store, "hebdo", AUJ)
    assert len(hebdo.evenements) == 1 and hebdo.clotures_proches(10)

    html = rendre_html(hebdo, cfg)
    md = rendre_markdown(hebdo, cfg)
    sms = rendre_texte_court(hebdo, cfg)
    for sortie in (html, md, sms[0]):
        assert "fiscalité des salaires" in sortie
    assert "Du 12 au 13 novembre 2026" in md
    assert "150 000 FCFA" in md
    assert "S'inscrire" in html


def test_texte_court_decoupe(cfg, store):
    for i in range(40):
        ev, _ = store.integrer(extrait(titre=f"Formation numéro {i} {'x' * i}", date_debut=f"2026-11-{i % 28 + 1:02d}",
                                       date_fin=None))
        evaluer(ev, cfg, AUJ)
    b = construire_bulletin(cfg, store, "mensuel", AUJ)
    messages = rendre_texte_court(b, cfg, limite=1500)
    assert len(messages) > 1 and all(len(m) <= 1500 for m in messages)


def test_calendrier(cfg):
    assert periodes_du_jour(cfg, date(2026, 10, 19)) == ["hebdo"]      # lundi
    assert periodes_du_jour(cfg, date(2026, 11, 1)) == ["mensuel"]     # 1er du mois (dimanche)
    assert periodes_du_jour(cfg, date(2026, 10, 21)) == []
    cfg.frequence = "quotidien"
    assert periodes_du_jour(cfg, date(2026, 10, 21)) == ["quotidien"]
    cfg.frequence = "mensuel"
    assert periodes_du_jour(cfg, date(2026, 10, 19)) == []


def test_chaine_complete_sans_ia(cfg, store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    docs = [RawDocument(source="page", url="https://lancaster.example/agenda", titre="Conférence leadership",
                        texte="Conférence sur le leadership au Lancaster Ouaga 2000 le 4 décembre 2026, "
                              "entrée libre. Contact : 70 11 22 33")]
    bilan = traiter_documents(docs, cfg, store, AUJ, verifier_les_liens=False)
    assert bilan.nouveaux == 1
    ev = store.tous()[0]
    assert ev.gratuit and ev.lieu_surveille == "Lancaster Ouaga 2000"
    assert ev.date_debut == "2026-12-04"


def test_lecture_flux_rss(cfg):
    import httpx

    from veille.sources import collecter_actualites

    rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>Atelier de formation au Bravia Hotel - Lefaso.net</title>
    <link>https://lefaso.net/atelier</link><pubDate>Mon, 12 Oct 2026 08:00:00 GMT</pubDate>
    <description>&lt;a href="x"&gt;Atelier le 20 novembre 2026 à Ouagadougou&lt;/a&gt;</description></item>
    </channel></rss>"""
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=rss)))
    cfg.requetes_actualites = ["a", "b"]
    docs = collecter_actualites(cfg, http)
    assert len(docs) == 1  # même lien vu par deux requêtes → dédoublonné
    assert docs[0].texte == "Atelier le 20 novembre 2026 à Ouagadougou"
