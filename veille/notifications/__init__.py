"""Envoi du bulletin sur les canaux configurés (e-mail, Telegram, WhatsApp)."""

from __future__ import annotations

import logging
import os
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import httpx

from ..config import Config
from ..rapport import Bulletin, rendre_html, rendre_markdown, rendre_texte_court

log = logging.getLogger(__name__)


class ErreurConfiguration(RuntimeError):
    pass


def _env(nom: str, requis: bool = True) -> str:
    valeur = os.environ.get(nom, "").strip()
    if requis and not valeur:
        raise ErreurConfiguration(f"Variable d'environnement manquante : {nom}")
    return valeur


# --------------------------------------------------------------------------- #
# E-mail (SMTP : Gmail, Outlook, Brevo, Mailjet, serveur d'entreprise...)
# --------------------------------------------------------------------------- #
def envoyer_email(b: Bulletin, cfg: Config) -> None:
    hote = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", requis=False) or 587)
    utilisateur = _env("SMTP_USER")
    mot_de_passe = _env("SMTP_PASSWORD")
    destinataires = [a.strip() for a in _env("EMAIL_TO").split(",") if a.strip()]
    expediteur = _env("EMAIL_FROM", requis=False) or utilisateur

    msg = EmailMessage()
    msg["Subject"] = f"{b.titre} · {len(b.evenements)} événement(s)"
    msg["From"] = formataddr((cfg.profil.nom, expediteur))
    msg["To"] = ", ".join(destinataires)
    msg["Message-ID"] = make_msgid(domain=expediteur.split("@")[-1])
    msg.set_content(rendre_markdown(b, cfg))
    msg.add_alternative(rendre_html(b, cfg), subtype="html")

    if port == 465:
        with smtplib.SMTP_SSL(hote, port, timeout=30) as smtp:
            smtp.login(utilisateur, mot_de_passe)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(hote, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(utilisateur, mot_de_passe)
            smtp.send_message(msg)
    log.info("E-mail envoyé à %s", ", ".join(destinataires))


# --------------------------------------------------------------------------- #
# Telegram (bot gratuit créé via @BotFather)
# --------------------------------------------------------------------------- #
def envoyer_telegram(b: Bulletin, cfg: Config, http: httpx.Client) -> None:
    jeton = _env("TELEGRAM_BOT_TOKEN")
    chats = [c.strip() for c in _env("TELEGRAM_CHAT_ID").split(",") if c.strip()]
    for chat in chats:
        for texte in rendre_texte_court(b, cfg, limite=3800):
            resp = http.post(
                f"https://api.telegram.org/bot{jeton}/sendMessage",
                json={"chat_id": chat, "text": texte, "disable_web_page_preview": True},
            )
            if resp.status_code != 200:
                aide = ""
                if chat == jeton.split(":")[0]:
                    aide = (" — TELEGRAM_CHAT_ID est l'identifiant du bot, pas le vôtre : envoyez /start "
                            "au bot puis lisez votre id sur https://api.telegram.org/bot<JETON>/getUpdates")
                raise RuntimeError(f"Telegram {resp.status_code} : {resp.text[:200]}{aide}")
            time.sleep(0.5)
    log.info("Telegram : bulletin envoyé à %d conversation(s)", len(chats))


# --------------------------------------------------------------------------- #
# WhatsApp — Twilio (API officielle) ou CallMeBot (gratuit, usage personnel)
# --------------------------------------------------------------------------- #
def envoyer_whatsapp(b: Bulletin, cfg: Config, http: httpx.Client) -> None:
    messages = rendre_texte_court(b, cfg, limite=1500)
    if cfg.whatsapp_fournisseur == "twilio":
        sid, jeton = _env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN")
        de, vers = _env("TWILIO_WHATSAPP_FROM"), _env("WHATSAPP_TO")
        for texte in messages:
            resp = http.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                auth=(sid, jeton),
                data={"From": f"whatsapp:{de}", "To": f"whatsapp:{vers}", "Body": texte},
            )
            if resp.status_code >= 300:
                raise RuntimeError(f"Twilio {resp.status_code} : {resp.text[:200]}")
            time.sleep(1)
    else:
        telephone, cle = _env("WHATSAPP_TO"), _env("CALLMEBOT_APIKEY")
        for texte in messages:
            resp = http.get(
                "https://api.callmebot.com/whatsapp.php",
                params={"phone": telephone, "text": texte, "apikey": cle},
            )
            if resp.status_code >= 300:
                raise RuntimeError(f"CallMeBot {resp.status_code} : {resp.text[:200]}")
            time.sleep(3)  # CallMeBot limite le débit
    log.info("WhatsApp (%s) : %d message(s) envoyé(s)", cfg.whatsapp_fournisseur, len(messages))


def diffuser(b: Bulletin, cfg: Config) -> dict[str, str]:
    """Envoie sur chaque canal. Un canal en échec n'empêche pas les autres."""
    resultats: dict[str, str] = {}
    with httpx.Client(timeout=30) as http:
        for canal in cfg.canaux:
            try:
                if canal == "email":
                    envoyer_email(b, cfg)
                elif canal == "telegram":
                    envoyer_telegram(b, cfg, http)
                elif canal == "whatsapp":
                    envoyer_whatsapp(b, cfg, http)
                resultats[canal] = "ok"
            except Exception as exc:  # noqa: BLE001 — on journalise et on continue
                log.error("Échec de l'envoi %s : %s", canal, exc)
                resultats[canal] = f"échec : {exc}"
    return resultats
