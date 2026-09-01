"""Create a single bcrypt-backed test account in HAYETT_USER (explicit, one-off).

Usage (interactive password prompt):
    python tools/create_test_account.py --email test.client@hayett.tn

Or with the password via environment (never on the command line):
    set TEST_ACCOUNT_PASSWORD=...   (PowerShell)
    python tools/create_test_account.py --email test.client@hayett.tn

Safety rules:
- READ-ONLY on every table EXCEPT the single INSERT into COMPTE.
- Never touches SYS.*, never creates/alters/drops any table.
- Picks an EXISTING CLIENT.client_id that has no linked COMPTE row yet.
- Generates a REAL bcrypt hash via backend security.hash_password().
- New compte_id = MAX(compte_id)+1 with a collision check before INSERT.
- The password is never printed, never logged, never written to a file.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ORACLE_DSN, ORACLE_PASSWORD, ORACLE_USER
from database import _connect
from security import hash_password


def _fetch_one(cur, sql, params=None):
    cur.execute(sql, params or {})
    row = cur.fetchone()
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Email of the test account.")
    args = parser.parse_args()

    if not (ORACLE_USER and ORACLE_PASSWORD and ORACLE_DSN):
        print("[create_test_account] Oracle is not configured in .env — aborting.")
        return 1

    password = os.getenv("TEST_ACCOUNT_PASSWORD")
    if password is None:
        password = getpass.getpass("Password for the test account (hidden): ")

    conn = _connect()
    try:
        with conn.cursor() as cur:
            # 1. Existing client WITHOUT a linked compte.
            row = _fetch_one(
                cur,
                "SELECT client_id FROM client "
                "WHERE client_id NOT IN (SELECT client_id FROM compte "
                "                          WHERE client_id IS NOT NULL) "
                "ORDER BY client_id",
            )
            if row is None:
                print("[create_test_account] No CLIENT without an existing COMPTE row.")
                print("                       Nothing inserted — aborting.")
                return 1
            client_id = row[0]

            # 2. Next compte_id with an explicit collision check.
            row = _fetch_one(cur, "SELECT COALESCE(MAX(compte_id), 0) FROM compte")
            new_id = int(row[0] or 0) + 1
            row = _fetch_one(
                cur, "SELECT COUNT(*) FROM compte WHERE compte_id = :cid",
                {"cid": new_id},
            )
            if (row[0] or 0) > 0:
                print("[create_test_account] compte_id collision at", new_id,
                      "— nothing inserted.")
                return 1

            # 3. Real bcrypt hash straight from the backend's own function.
            password_hash = hash_password(password)

            # 4. Single controlled INSERT into COMPTE. Nothing else changes.
            cur.execute(
                "INSERT INTO compte (compte_id, email, password_hash, client_id) "
                "VALUES (:1, :2, :3, :4)",
                [new_id, args.email, password_hash, client_id],
            )
        conn.commit()
    finally:
        conn.close()

    # NEVER print the password or its hash here.
    print("[create_test_account] COMPTE row inserted.")
    print(f"  compte_id = {new_id}")
    print(f"  email     = {args.email}")
    print(f"  client_id = {client_id} (existing CLIENT, read from Oracle)")
    print("Use these credentials in the /login smoke test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())