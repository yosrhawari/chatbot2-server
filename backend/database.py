import logging

logger = logging.getLogger(__name__)

import threading

import oracledb

from config import (
    ORACLE_USER,
    ORACLE_PASSWORD,
    ORACLE_DSN,
    SEED_DB_ON_START,
)
from security import hash_password


class DatabaseUnavailable(RuntimeError):
    """Raised when Oracle is not configured or unreachable."""


_CONFIGURED = all([ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN])
_db_ok: bool | None = None
_init_lock = threading.Lock()


def configured() -> bool:
    return _CONFIGURED


def db_available() -> bool:
    """True when Oracle is reachable. Optimistic before first init failure."""
    if not _CONFIGURED:
        return False
    return _db_ok is not False


def _connect():
    if not _CONFIGURED:
        raise DatabaseUnavailable(
            "Oracle is not configured: set ORACLE_DSN, ORACLE_USER and "
            "ORACLE_PASSWORD in the server .env."
        )
    return oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)


# ── Schema (plan §9-§14) ─────────────────────────────────────────────────────
# The HAYETT_USER tables already exist in Oracle and are the SOURCE OF TRUTH.
# _SCHEMA is kept ONLY for the explicit opt-in seed flow (SEED_DB_ON_START=1)
# against an empty database. Normal startup never creates, alters or drops a
# table (strictly read-only verification).
_SCHEMA = [
    """CREATE TABLE client (
        client_id       NUMBER PRIMARY KEY,
        cin             VARCHAR2(20),
        nom             VARCHAR2(100) NOT NULL,
        prenom          VARCHAR2(100) NOT NULL,
        date_naissance  DATE,
        telephone       VARCHAR2(30)
    )""",
    """CREATE TABLE contrat (
        contrat_id          NUMBER PRIMARY KEY,
        client_id           NUMBER NOT NULL REFERENCES client(client_id),
        date_souscription   DATE,
        duree               NUMBER,
        periodicite         VARCHAR2(20),
        montant_prime       NUMBER(12,3),
        statut              VARCHAR2(20)
    )""",
    """CREATE TABLE versement (
        versement_id    NUMBER PRIMARY KEY,
        contrat_id      NUMBER NOT NULL REFERENCES contrat(contrat_id),
        date_versement  DATE,
        montant         NUMBER(12,3),
        type_versement  VARCHAR2(50)
    )""",
    """CREATE TABLE epargne (
        epargne_id               NUMBER PRIMARY KEY,
        contrat_id               NUMBER NOT NULL REFERENCES contrat(contrat_id),
        date_calcul              DATE,
        montant_epargne          NUMBER(12,3),
        participation_benefices  NUMBER(12,3)
    )""",
    """CREATE TABLE beneficiaire (
        beneficiaire_id  NUMBER PRIMARY KEY,
        contrat_id       NUMBER NOT NULL REFERENCES contrat(contrat_id),
        nom              VARCHAR2(100),
        prenom           VARCHAR2(100),
        relation         VARCHAR2(100)
    )""",
    """CREATE TABLE compte (
        compte_id       NUMBER PRIMARY KEY,
        email           VARCHAR2(255) UNIQUE NOT NULL,
        password_hash   VARCHAR2(255) NOT NULL,
        client_id       NUMBER NOT NULL REFERENCES client(client_id)
    )""",
]

_TABLES = ("client", "contrat", "versement", "epargne", "beneficiaire", "compte")


def _table_exists(conn, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM user_tables WHERE table_name = UPPER(:name)",
            {"name": name},
        )
        return cur.fetchone()[0] > 0


# Expected columns per table (plan §9-§14). Used ONLY for read-only
# verification against the real HAYETT_USER tables.
_EXPECTED_COLUMNS = {
    "COMPTE": {"compte_id", "email", "password_hash", "client_id"},
    "CLIENT": {"client_id", "cin", "nom", "prenom", "date_naissance", "telephone"},
    "CONTRAT": {"contrat_id", "client_id", "date_souscription", "duree",
                "periodicite", "montant_prime", "statut"},
    "VERSEMENT": {"versement_id", "contrat_id", "date_versement", "montant",
                  "type_versement"},
    "EPARGNE": {"epargne_id", "contrat_id", "date_calcul", "montant_epargne",
                "participation_benefices"},
    "BENEFICIAIRE": {"beneficiaire_id", "contrat_id", "nom", "prenom", "relation"},
}


def check_schema(conn) -> dict:
    """STRICTLY READ-ONLY schema verification against the real HAYETT_USER
    tables. Never creates, alters or drops anything.

    Returns {"ok": bool, "problems": [str], "tables": {name: {columns}}}.
    """
    problems = []
    tables = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name FROM user_tab_columns "
            "WHERE table_name IN ('COMPTE','CLIENT','CONTRAT','VERSEMENT',"
            "'EPARGNE','BENEFICIAIRE') ORDER BY table_name, column_id"
        )
        for table_name, column_name in cur.fetchall():
            tables.setdefault(table_name.upper(), set()).add(column_name.upper())

    for table in _EXPECTED_COLUMNS:
        actual = tables.get(table)
        if actual is None:
            problems.append(f"Missing table {table}")
            continue
        missing = {c.upper() for c in _EXPECTED_COLUMNS[table]} - actual
        if missing:
            problems.append(f"Table {table} missing columns: {sorted(missing)}")
    return {
        "ok": not problems,
        "problems": problems,
        "tables": {k: sorted(v) for k, v in tables.items()},
    }


def ensure_schema(conn) -> None:
    """Create every table of the plan when it does not exist yet (idempotent).

    Intended ONLY for the explicit opt-in seed flow against an EMPTY database.
    Never called when SEED_DB_ON_START=0; never alters an existing table.
    """
    for ddl in _SCHEMA:
        name = ddl.split()[2].upper()
        if _table_exists(conn, name):
            continue
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
        logger.info(f"[DB] Created table {name}")


# ── Seed data (test clients, contracts, savings, payments) ───────────────────
def _seed_data(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM compte")
        if cur.fetchone()[0] > 0:
            logger.info("[DB] Accounts already present, skipping seed.")
            return

        cur.execute(
            "INSERT INTO client (client_id, cin, nom, prenom, date_naissance, telephone) "
            "VALUES (:1, :2, :3, :4, TO_DATE(:5, 'YYYY-MM-DD'), :6)",
            [1001, "CIN1001", "Ben Salah", "Ahmed", "1985-03-14", "+216 98 123 456"],
        )
        cur.execute(
            "INSERT INTO client (client_id, cin, nom, prenom, date_naissance, telephone) "
            "VALUES (:1, :2, :3, :4, TO_DATE(:5, 'YYYY-MM-DD'), :6)",
            [1002, "CIN1002", "Trabelsi", "Fatma", "1990-07-02", "+216 22 887 654"],
        )
        cur.execute(
            "INSERT INTO compte (compte_id, email, password_hash, client_id) "
            "VALUES (:1, :2, :3, :4)",
            [1, "ahmed@gmail.com", hash_password("secret123"), 1001],
        )
        cur.execute(
            "INSERT INTO compte (compte_id, email, password_hash, client_id) "
            "VALUES (:1, :2, :3, :4)",
            [2, "fatma@gmail.com", hash_password("secret456"), 1002],
        )
        # Ahmed: souscription ~4 ans ago, prime mensuelle 250 DT.
        cur.execute(
            "INSERT INTO contrat (contrat_id, client_id, date_souscription, duree, "
            "periodicite, montant_prime, statut) "
            "VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5, :6, :7)",
            [1, 1001, "2022-08-01", 20, "MENSUEL", 250.0, "ACTIF"],
        )
        # Fatma: second client used by the isolation tests.
        cur.execute(
            "INSERT INTO contrat (contrat_id, client_id, date_souscription, duree, "
            "periodicite, montant_prime, statut) "
            "VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5, :6, :7)",
            [2, 1002, "2024-01-15", 15, "MENSUEL", 100.0, "ACTIF"],
        )
        # Épargne — Ahmed's latest balance is 18 500 DT (plan §19).
        epargne_rows = [
            (1, 1, "2022-12-31", 2000.0, 120.0),
            (2, 1, "2023-12-31", 6000.0, 430.0),
            (3, 1, "2024-12-31", 10500.0, 700.0),
            (4, 1, "2025-12-31", 15000.0, 980.0),
            (5, 1, "2026-06-30", 18500.0, 1250.0),
            (6, 2, "2026-06-30", 6000.0, 410.0),
        ]
        for row in epargne_rows:
            cur.execute(
                "INSERT INTO epargne (epargne_id, contrat_id, date_calcul, "
                "montant_epargne, participation_benefices) "
                "VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5)",
                list(row),
            )
        # Versements 2026 (6 mensualités de 250 DT pour Ahmed).
        for month in range(1, 7):
            cur.execute(
                "INSERT INTO versement (versement_id, contrat_id, date_versement, "
                "montant, type_versement) "
                "VALUES (:1, :2, TO_DATE(:3, 'YYYY-MM-DD'), :4, :5)",
                [month, 1, f"2026-{month:02d}-01", 250.0, "PRIME"],
            )
        cur.execute(
            "INSERT INTO beneficiaire (beneficiaire_id, contrat_id, nom, prenom, relation) "
            "VALUES (:1, :2, :3, :4, :5)",
            [1, 1, "Ben Salah", "Sami", "Enfant"],
        )
        cur.execute(
            "INSERT INTO beneficiaire (beneficiaire_id, contrat_id, nom, prenom, relation) "
            "VALUES (:1, :2, :3, :4, :5)",
            [2, 1, "Ben Salah", "Nour", "Enfant"],
        )
        conn.commit()
        logger.info("[DB] Seed data inserted (ahmed@gmail.com / fatma@gmail.com).")


def init_db() -> bool:
    """Verify the Oracle connection and the HAYETT_USER schema. STRICTLY
    READ-ONLY unless SEED_DB_ON_START=1 (explicit opt-in, empty DB only).

    Normal startup never creates, alters or drops tables and never seeds data:
    the existing HAYETT_USER tables are the source of truth. On failure the
    DB-dependent routes refuse with a clear message."""
    global _db_ok
    with _init_lock:
        if not _CONFIGURED:
            _db_ok = False
            logger.warning("[DB] Oracle not configured; login and personal analytics"
                           " are disabled.")
            return False
        try:
            conn = _connect()
            try:
                report = check_schema(conn)
                if report["ok"]:
                    logger.info("[DB] Oracle ready — HAYETT_USER schema verified "
                                f"({len(report['tables'])} tables).")
                else:
                    _db_ok = False
                    for problem in report["problems"]:
                        logger.error(f"[DB] Schema problem: {problem}")
                    return False
                # Seed is an EXPLICIT, opt-in operation. In the default
                # configuration (SEED_DB_ON_START=0) it NEVER runs, so the
                # current HAYETT_USER data is never touched.
                if SEED_DB_ON_START:
                    ensure_schema(conn)
                    _seed_data(conn)
                _db_ok = True
                return True
            finally:
                conn.close()
        except Exception as e:
            _db_ok = False
            logger.error(f"[DB] Oracle unavailable: {e}")
            return False


# ── Row helpers ──────────────────────────────────────────────────────────────
def _rows_to_dicts(cursor) -> list:
    cols = [c[0].lower() for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _fetch(sql: str, params: dict) -> list:
    if not db_available():
        raise DatabaseUnavailable("Oracle unavailable.")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return _rows_to_dicts(cur)
    finally:
        conn.close()


# ── Controlled queries (every one filters by :client_id) ─────────────────────
def find_account_by_email(email: str) -> dict | None:
    """Auth lookup: compte + client by email. Returns password_hash, client_id,
    nom, prenom or None."""
    rows = _fetch(
        """SELECT c.compte_id, c.client_id, c.password_hash, cl.nom, cl.prenom
           FROM compte c
           JOIN client cl ON cl.client_id = c.client_id
           WHERE LOWER(c.email) = LOWER(:email)""",
        {"email": email or ""},
    )
    return rows[0] if rows else None


def find_client_by_id(client_id) -> dict | None:
    rows = _fetch(
        "SELECT client_id, cin, nom, prenom, telephone "
        "FROM client WHERE client_id = :client_id",
        {"client_id": client_id},
    )
    return rows[0] if rows else None


def find_client_by_cin(cin: str) -> dict | None:
    """Admin lookup: client by CIN (carte d'identité nationale).
    Returns client_id, nom, prenom or None when not found."""
    rows = _fetch(
        "SELECT client_id, cin, nom, prenom FROM client WHERE cin = :cin",
        {"cin": cin or ""},
    )
    return rows[0] if rows else None


def list_contrats(client_id) -> list:
    return _fetch(
        """SELECT contrat_id, date_souscription, duree, periodicite,
                  montant_prime, statut
           FROM contrat
           WHERE client_id = :client_id
           ORDER BY date_souscription""",
        {"client_id": client_id},
    )


def latest_epargne(client_id) -> list:
    """Latest savings balance per contract — plan §19 example."""
    return _fetch(
        """SELECT e.contrat_id, e.date_calcul, e.montant_epargne,
                  e.participation_benefices
           FROM epargne e
           JOIN contrat c ON c.contrat_id = e.contrat_id
           WHERE c.client_id = :client_id
             AND e.date_calcul = (SELECT MAX(e2.date_calcul)
                                  FROM epargne e2
                                  WHERE e2.contrat_id = e.contrat_id)
           ORDER BY e.contrat_id""",
        {"client_id": client_id},
    )


def versements_annee(client_id, year: int) -> list:
    """Total paid per contract during a calendar year."""
    return _fetch(
        """SELECT c.contrat_id, COUNT(*) AS nb_versements,
                  SUM(v.montant) AS total_verse
           FROM versement v
           JOIN contrat c ON c.contrat_id = v.contrat_id
           WHERE c.client_id = :client_id
             AND EXTRACT(YEAR FROM v.date_versement) = :year
           GROUP BY c.contrat_id
           ORDER BY c.contrat_id""",
        {"client_id": client_id, "year": year},
    )


def list_beneficiaires(client_id) -> list:
    return _fetch(
        """SELECT b.contrat_id, b.nom, b.prenom, b.relation
           FROM beneficiaire b
           JOIN contrat c ON c.contrat_id = b.contrat_id
           WHERE c.client_id = :client_id
           ORDER BY b.contrat_id, b.beneficiaire_id""",
        {"client_id": client_id},
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ok = init_db()
    raise SystemExit(0 if ok else 1)