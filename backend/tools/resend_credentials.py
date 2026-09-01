#!/usr/bin/env python3
"""Re-envoi des identifiants HAYETT par email (outil admin CLI).

Le mot de passe initial n'est PAS récupérable depuis PASSWORD_HASH (bcrypt).
L'admin doit le ressaisir via getpass().

Usage:
    python -m tools.resend_credentials --email ahmed@gmail.com
    # → vérifie que le compte existe, demande le mot de passe, envoie l'email.

    python -m tools.resend_credentials --email ahmed@gmail.com --prenom Ahmed --nom "Ben Salah"
    # → surcharge prenom/nom si besoin (sinon lus depuis Oracle CLIENT).
"""
import argparse
import getpass
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import ORACLE_DSN, ORACLE_PASSWORD, ORACLE_USER

import oracledb


def _connect():
    return oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-envoi email identifiants HAYETT")
    parser.add_argument("--email", required=True, help="Email du compte existant")
    parser.add_argument("--prenom", default=None, help="Prenom (optionnel, sinon lu depuis CLIENT)")
    parser.add_argument("--nom", default=None, help="Nom (optionnel, sinon lu depuis CLIENT)")
    args = parser.parse_args()

    if not (ORACLE_USER and ORACLE_PASSWORD and ORACLE_DSN):
        print("[ERROR] Oracle non configure dans .env")
        return 1

    email = args.email.strip().lower()
    if not email:
        print("[ERROR] Email requis")
        return 1

    # Verifie que le compte existe et recupere prenom/nom depuis CLIENT
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cl.prenom, cl.nom FROM compte c "
                    "JOIN client cl ON cl.client_id = c.client_id "
                    "WHERE LOWER(c.email) = LOWER(:email)",
                    {"email": email},
                )
                row = cur.fetchone()
                if row is None:
                    print(f"[ERROR] Aucun compte trouve pour email={email}")
                    return 1
                db_prenom, db_nom = row[0], row[1]
        finally:
            conn.close()
    except Exception as e:
        print(f"[ERROR] Erreur Oracle: {e}")
        return 1

    prenom = args.prenom.strip() if args.prenom else db_prenom
    nom = args.nom.strip() if args.nom else db_nom

    password = getpass.getpass("Mot de passe initial a envoyer : ")
    if not password:
        print("[ERROR] Mot de passe requis")
        return 1
    confirm = getpass.getpass("Confirmer mot de passe : ")
    if password != confirm:
        print("[ERROR] Mots de passe differents")
        return 1

    try:
        from email_service import is_email_configured, send_welcome_email

        if not is_email_configured():
            print("[ERROR] Email non configure (SMTP_HOST/SMTP_FROM vides dans .env).")
            return 1

        send_welcome_email(email, prenom, nom, password)
        print(f"\n[OK] Email renvoye a {email}")
    except Exception as e:
        print(f"[ERROR] Echec envoi email a {email}: {e}")
        return 2
    finally:
        try:
            del password
            del confirm
        except NameError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
