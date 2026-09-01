#!/usr/bin/env python3
"""Migration one-off : remplacer HASH_TEST_* par vrais hashes bcrypt.

- Un mot de passe temporaire DIFFERENT par compte (getpass par compte)
- UPDATE COMPTE SET password_hash = :new_hash WHERE compte_id = :id
- Ne touche JAMAIS : CLIENT, CONTRAT, VERSEMENT, EPARGNE, BENEFICIAIRE

Usage:
    # Depuis la racine du projet :
    python tools/migrate_test_passwords.py
"""
import getpass
import sys
from pathlib import Path

# Ensure backend package is importable when run from project root
# __file__ is in tools/, backend is at project_root/backend
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import oracledb

from config import ORACLE_DSN, ORACLE_PASSWORD, ORACLE_USER
from security import hash_password


def _connect():
    return oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)


def main() -> int:
    if not (ORACLE_USER and ORACLE_PASSWORD and ORACLE_DSN):
        print("[ERROR] Oracle non configure dans .env (ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN)")
        return 1

    print("=== Migration HASH_TEST_* -> bcrypt (comptes de test) ===\n")

    comptes_a_migrer = [
        (1, "ahmed.benali@test.com"),
        (2, "sarah.trabelsi@test.com"),
        (3, "mohamed.mansour@test.com"),
    ]

    conn = _connect()
    try:
        for compte_id, email in comptes_a_migrer:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT password_hash FROM compte WHERE compte_id = :cid",
                    {"cid": compte_id},
                )
                row = cur.fetchone()
                if not row:
                    print(f"[WARN] Compte {compte_id} ({email}) introuvable -- ignore")
                    continue

                current_hash = row[0]
                if current_hash and current_hash.startswith("$2b$"):
                    print(f"[OK] {email} : hash deja bcrypt -- ignore")
                    continue

            print(f"\n--- Compte {compte_id} : {email} ---")
            print(f"   Hash actuel : {current_hash}")

            pwd = getpass.getpass(f"Mot de passe temporaire pour {email} : ")
            if not pwd:
                print("[ERROR] Mot de passe vide -- migration annulee")
                conn.rollback()
                return 1

            new_hash = hash_password(pwd)

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE compte SET password_hash = :newhash WHERE compte_id = :cid",
                    {"newhash": new_hash, "cid": compte_id},
                )

            print(f"   [OK] Hash bcrypt genere et mis a jour")

        conn.commit()
        print("\n=== Migration terminee avec succes ===")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Erreur : {e}")
        return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())