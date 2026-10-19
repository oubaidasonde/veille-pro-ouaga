"""Interface en ligne de commande : `python -m veille <commande>`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .config import load_config
from .notifications import diffuser
from .pipeline import archiver, collecter, construire_bulletin, marquer_envoye, periodes_du_jour
from .rapport import rendre_texte_court
from .store import DEFAULT_STORE, Store

log = logging.getLogger("veille")


def _charger_env(path: str = ".env") -> None:
    """Charge un fichier .env local (KEY=valeur) sans écraser les variables déjà définies."""
    try:
        with open(path, encoding="utf-8") as fh:
            for ligne in fh:
                ligne = ligne.strip()
                if ligne and not ligne.startswith("#") and "=" in ligne:
                    cle, valeur = ligne.split("=", 1)
                    if valeur.strip():
                        os.environ.setdefault(cle.strip(), valeur.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def _aujourdhui(cfg, valeur: str | None) -> date:
    if valeur:
        return date.fromisoformat(valeur)
    return datetime.now(ZoneInfo(cfg.profil.fuseau_horaire)).date()


def _publier(cfg, store, periode, jour, envoyer: bool, ignorer_vide: bool) -> int:
    b = construire_bulletin(cfg, store, periode, jour)
    md, html = archiver(b, cfg)
    log.info("%s : %d événement(s) → %s", b.titre, len(b.evenements), md.name)
    if not envoyer:
        print("\n\n".join(rendre_texte_court(b, cfg)))
        return 0
    if ignorer_vide and not b.evenements:
        log.info("Aucun nouvel événement : pas d'envoi pour %s.", periode)
        return 0
    resultats = diffuser(b, cfg)
    if any(r == "ok" for r in resultats.values()):
        marquer_envoye(b, store)
    echecs = {c: r for c, r in resultats.items() if r != "ok"}
    return 1 if echecs and len(echecs) == len(resultats) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="veille", description="Agent de veille événementielle — Ouagadougou")
    p.add_argument("--config", help="Chemin du fichier de configuration")
    p.add_argument("--base", default=str(DEFAULT_STORE), help="Fichier JSON des événements")
    p.add_argument("--date", help="Forcer la date du jour (AAAA-MM-JJ), utile pour les tests")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="commande", required=True)

    sub.add_parser("collecter", help="Rechercher, extraire, vérifier et enregistrer les événements")

    pb = sub.add_parser("bulletin", help="Produire (et éventuellement envoyer) un bulletin")
    pb.add_argument("--periode", choices=["quotidien", "hebdo", "mensuel"], default="mensuel")
    pb.add_argument("--envoyer", action="store_true", help="Envoyer sur les canaux configurés")

    sub.add_parser("planifie", help="Exécution quotidienne : collecte + bulletins prévus ce jour")
    sub.add_parser("test-envoi", help="Envoyer un message de test sur chaque canal")
    sub.add_parser("liste", help="Afficher les événements enregistrés")

    args = p.parse_args(argv)
    _charger_env()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s")
    for bruyant in ("httpx", "httpx2", "httpcore", "anthropic", "google_genai"):
        logging.getLogger(bruyant).setLevel(logging.WARNING)

    cfg = load_config(args.config)
    store = Store(args.base)
    jour = _aujourdhui(cfg, args.date)

    if args.commande == "collecter":
        bilan = collecter(cfg, store, jour)
        log.info("Collecte : %d documents, %d extraits, %d nouveaux, %d mis à jour, %d en base",
                 bilan.documents, bilan.extraits, bilan.nouveaux, bilan.mis_a_jour, bilan.total)
        return 0

    if args.commande == "bulletin":
        return _publier(cfg, store, args.periode, jour, args.envoyer, ignorer_vide=False)

    if args.commande == "planifie":
        bilan = collecter(cfg, store, jour)
        log.info("Collecte : %d nouveaux événements (%d en base)", bilan.nouveaux, bilan.total)
        code = 0
        for periode in periodes_du_jour(cfg, jour):
            code |= _publier(cfg, store, periode, jour, envoyer=True,
                             ignorer_vide=periode == "quotidien")
        return code

    if args.commande == "test-envoi":
        from .rapport import Bulletin
        b = Bulletin("quotidien", jour, [])
        resultats = diffuser(b, cfg)
        for canal, r in resultats.items():
            print(f"{canal:10s} {r}")
        return 0 if all(r == "ok" for r in resultats.values()) else 1

    if args.commande == "liste":
        for e in sorted(store.tous(), key=lambda e: e.date_debut or "9999"):
            print(f"[{e.statut:10s}] {e.score_confiance:3d}  {e.date_debut or '????-??-??'}  "
                  f"{e.titre}  @ {e.lieu or '?'}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
