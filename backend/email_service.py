import logging
import smtplib
import socket
from email.message import EmailMessage

from config import (
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_TIMEOUT,
    SMTP_USERNAME,
    SMTP_USE_TLS,
)

logger = logging.getLogger(__name__)


def is_email_configured() -> bool:
    """True when minimal SMTP settings are present."""
    return bool(SMTP_HOST and SMTP_FROM)


def build_welcome_email(prenom: str, nom: str, email: str, password: str) -> EmailMessage:
    """Construit l'email de bienvenue HAYETT. Le mot de passe est inclus
    en clair UNIQUEMENT dans le corps de ce message — jamais persiste."""
    subject = "Bienvenue dans votre espace client HAYETT"
    text_body = (
        f"Bonjour {prenom} {nom},\n"
        f"\n"
        f"Votre compte client HAYETT a \u00e9t\u00e9 cr\u00e9\u00e9.\n"
        f"\n"
        f"Voici vos informations de connexion :\n"
        f"\n"
        f"Email : {email}\n"
        f"Mot de passe initial : {password}\n"
        f"\n"
        f"Vous pouvez utiliser ces informations pour vous connecter \u00e0 l'application.\n"
        f"\n"
        f"Pour des raisons de s\u00e9curit\u00e9, veuillez conserver ces informations de mani\u00e8re confidentielle.\n"
        f"\n"
        f"Cordialement,\n"
        f"HAYETT\n"
    )
    html_body = (
        f"<p>Bonjour {prenom} {nom},</p>"
        f"<p>Votre compte client HAYETT a \u00e9t\u00e9 cr\u00e9\u00e9.</p>"
        f"<p>Voici vos informations de connexion :</p>"
        f"<ul><li><strong>Email :</strong> {email}</li>"
        f"<li><strong>Mot de passe initial :</strong> {password}</li></ul>"
        f"<p>Vous pouvez utiliser ces informations pour vous connecter \u00e0 l'application.</p>"
        f"<p>Pour des raisons de s\u00e9curit\u00e9, veuillez conserver ces informations de mani\u00e8re confidentielle.</p>"
        f"<p>Cordialement,<br>HAYETT</p>"
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    return msg


def send_welcome_email(to_email: str, prenom: str, nom: str, password: str) -> None:
    """Envoie l'email de bienvenue. Lève une exception en cas d'échec SMTP.

    Le mot de passe est transmis uniquement pour construire le message,
    jamais loggé.
    """
    if not is_email_configured():
        raise RuntimeError(
            "Email non configuré: renseignez SMTP_HOST et SMTP_FROM dans le .env du serveur."
        )

    msg = build_welcome_email(prenom, nom, to_email, password)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
            if SMTP_USE_TLS:
                server.starttls()
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info(f"[EMAIL] Bienvenue envoye a {to_email}")
    except (smtplib.SMTPException, socket.error, socket.timeout, OSError) as e:
        logger.error(f"[EMAIL] Echec envoi a {to_email}: {type(e).__name__}")
        raise
