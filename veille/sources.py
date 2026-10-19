"""Collecteurs : actualités (Google News RSS), pages suivies, recherche web par Claude."""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from urllib.parse import quote_plus

import feedparser
import httpx
from bs4 import BeautifulSoup

from .config import Config
from .llm import client_ia, texte_reponse
from .models import RawDocument

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; VeilleProOuaga/1.0; +https://github.com)"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
MAX_TEXTE_PAGE = 15_000


# --------------------------------------------------------------------------- #
# 1. Actualités — Google News RSS (gratuit, sans clé)
# --------------------------------------------------------------------------- #
def url_google_news(requete: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(requete + ' when:60d')}&hl=fr&gl=BF&ceid=BF:fr"


def collecter_actualites(cfg: Config, http: httpx.Client) -> list[RawDocument]:
    docs: dict[str, RawDocument] = {}
    for requete in cfg.requetes_actualites:
        try:
            resp = http.get(url_google_news(requete))
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Actualités « %s » indisponibles : %s", requete, exc)
            continue
        for entry in feedparser.parse(resp.content).entries:
            lien = entry.get("link", "")
            if not lien or lien in docs:
                continue
            resume = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
            docs[lien] = RawDocument(
                source="actualites",
                url=lien,
                titre=entry.get("title", ""),
                texte=resume,
                publie_le=entry.get("published"),
            )
    log.info("Actualités : %d articles", len(docs))
    return list(docs.values())


# --------------------------------------------------------------------------- #
# 2. Pages suivies — sites d'hôtels, agendas d'organismes
# --------------------------------------------------------------------------- #
def extraire_texte_html(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
        tag.decompose()
    titre = soup.title.get_text(strip=True) if soup.title else ""
    texte = " ".join(soup.get_text(" ", strip=True).split())
    return titre, texte[:MAX_TEXTE_PAGE]


def collecter_pages(cfg: Config, http: httpx.Client) -> list[RawDocument]:
    urls = list(dict.fromkeys(cfg.pages_suivies + [l.site for l in cfg.lieux if l.site]))
    docs = []
    for url in urls:
        try:
            resp = http.get(url, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Page %s inaccessible : %s", url, exc)
            continue
        titre, texte = extraire_texte_html(resp.text)
        if texte:
            docs.append(RawDocument(source="page", url=str(resp.url), titre=titre, texte=texte))
    log.info("Pages suivies : %d pages lues", len(docs))
    return docs


# --------------------------------------------------------------------------- #
# 3. Recherche web approfondie — Claude + outil web_search
# --------------------------------------------------------------------------- #
CONSIGNE_RECHERCHE = """Tu es un chargé de veille professionnelle basé à Ouagadougou (Burkina Faso).
Mission : trouver les formations, conférences, séminaires, ateliers, forums et rencontres
professionnelles À VENIR entre le {debut} et le {fin}, {zone}.

Priorités de recherche :
- Événements organisés dans les grands hôtels et centres de conférence : {lieux}.
- Annonces des organismes : {organismes}.
- Cabinets de conseil et organismes de formation burkinabè, chambres consulaires,
  associations professionnelles, organisations internationales, ambassades.
- Publications publiques LinkedIn et Facebook, plateformes d'inscription (Eventbrite,
  Google Forms, sites d'organisateurs), presse burkinabè (Lefaso.net, Sidwaya, Burkina24,
  L'Économiste du Faso, AIB, Wakat Séra).
Thématiques prioritaires : {themes}.

Règles :
- Ignore les événements déjà passés (antérieurs au {aujourdhui}) : ce ne sont que des « retours en images ».
- Pour chaque événement trouvé, donne : titre, type, dates, horaires, lieu exact, ville,
  organisateur, public cible, tarif, date limite et lien d'inscription, contact, et les URL sources.
- Écris « non publié » pour toute information introuvable. N'invente jamais une date, un prix ou un lien.
- Croise les sources quand c'est possible et indique si l'information vient de l'organisateur lui-même.
Réponds en français, un bloc par événement."""


def _consigne(cfg: Config, aujourdhui: date, focus: str = "") -> str:
    consigne = CONSIGNE_RECHERCHE.format(
        debut=aujourdhui.isoformat(),
        fin=(aujourdhui + timedelta(days=cfg.horizon_jours)).isoformat(),
        aujourdhui=aujourdhui.isoformat(),
        zone="à Ouagadougou uniquement" if cfg.zone == "ouagadougou"
        else "au Burkina Faso, en priorité à Ouagadougou",
        lieux=", ".join(l.nom for l in cfg.lieux),
        organismes=", ".join(cfg.organismes),
        themes=", ".join(sorted(cfg.thematiques.actives())),
    )
    return consigne + (f"\n\nPour cette recherche, concentre-toi sur : {focus}" if focus else "")


# Gemini fait une recherche Google par appel : on découpe la veille en angles complémentaires.
ANGLES_GEMINI = [
    "les événements annoncés dans les grands hôtels et centres de conférence de Ouagadougou",
    "les formations, séminaires et ateliers proposés par les cabinets, organismes de formation, "
    "chambres consulaires et institutions (inscriptions ouvertes)",
    "les conférences, forums et rencontres annoncés sur LinkedIn, Facebook et la presse burkinabè "
    "(Lefaso.net, Burkina24, Sidwaya, AIB)",
]


def _recherche_claude(cfg: Config, consigne: str) -> tuple[str, list[str]]:
    client = client_ia()
    tools = [{
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": cfg.recherche_ia.max_recherches,
        "user_location": {"type": "approximate", "city": "Ouagadougou", "country": "BF",
                          "timezone": "Africa/Ouagadougou"},
    }]
    messages: list = [{"role": "user", "content": consigne}]

    reponse = None
    for _ in range(5):  # reprise automatique des tours mis en pause par le serveur
        reponse = client.beta.messages.create(
            model=cfg.recherche_ia.modele,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": cfg.recherche_ia.effort},
            tools=tools,
            messages=messages,
        )
        if reponse.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": reponse.content}]

    if reponse is None or reponse.stop_reason == "refusal":
        return "", []
    sources = sorted({
        r.url
        for bloc in reponse.content if bloc.type == "web_search_tool_result"
        and isinstance(bloc.content, list)
        for r in bloc.content if getattr(r, "url", None)
    })
    return texte_reponse(reponse), sources


def collecter_recherche_ia(cfg: Config, aujourdhui: date) -> list[RawDocument]:
    if not (cfg.recherche_ia.active and cfg.ia_disponible):
        log.info("Recherche IA désactivée (pas de clé pour %s ou option inactive).", cfg.recherche_ia.fournisseur)
        return []

    if cfg.recherche_ia.fournisseur == "gemini":
        from . import gemini

        resultats = []
        for i, angle in enumerate(ANGLES_GEMINI):
            if i:
                time.sleep(gemini.PAUSE_ENTRE_APPELS)
            resultats.append(gemini.rechercher(_consigne(cfg, aujourdhui, angle), cfg.recherche_ia.modele_gemini))
    else:
        resultats = [_recherche_claude(cfg, _consigne(cfg, aujourdhui))]

    docs = []
    for i, (texte, sources) in enumerate(resultats, 1):
        log.info("Recherche IA %d : %d caractères, %d sources", i, len(texte), len(sources))
        if texte:
            docs.append(RawDocument(
                source="recherche_ia",
                url=f"recherche-ia://{aujourdhui.isoformat()}/{i}",
                titre="Synthèse de recherche web",
                texte=texte + "\n\nSources consultées :\n" + "\n".join(sources),
            ))
    return docs


def nouveau_client_http() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
                        timeout=TIMEOUT, follow_redirects=True)


def collecter_tout(cfg: Config, aujourdhui: date) -> list[RawDocument]:
    with nouveau_client_http() as http:
        docs = collecter_actualites(cfg, http) + collecter_pages(cfg, http)
    try:
        docs += collecter_recherche_ia(cfg, aujourdhui)
    except Exception as exc:  # noqa: BLE001 — la collecte continue sans la recherche IA
        log.error("Recherche IA en échec : %s", exc)
    return docs
