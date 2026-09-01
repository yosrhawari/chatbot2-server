"""Real-Oracle integration tests (HAYETT_USER is the source of truth).

Every test here is READ-ONLY against the real database and skips when Oracle
is not configured/reachable. client_ids are ALWAYS fetched dynamically from
COMPTE/CLIENT after authentication-lookup (never hardcoded, never assumed to
be 1001/1002). No seed is ever run by these tests.
"""
import pytest

import database

# conftest.py globally replaces sys.modules["database"] with a MagicMock to
# isolate the AI modules. These integration tests need the REAL database module
# (so they skip cleanly when Oracle is unconfigured). Restore it.
import sys as _sys  # noqa: E402
import importlib  # noqa: E402

if getattr(database, "__name__", "") != "database":
    _sys.modules.pop("database", None)
    database = importlib.import_module("database")


@pytest.fixture(scope="module")
def oracle_conn():
    """Connect once to the real HAYETT_USER. Skips when unavailable."""
    if not database.configured():
        pytest.skip("Oracle not configured (ORACLE_* missing in .env)")
    try:
        conn = database._connect()
    except Exception as e:
        pytest.skip(f"Oracle unreachable: {e}")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def accounts(oracle_conn):
    """[(client_id, email, password_hash)] from COMPTE+CLIENT — read-only."""
    rows = []
    with oracle_conn.cursor() as cur:
        cur.execute(
            "SELECT c.client_id, c.email, c.password_hash "
            "FROM compte c "
            "JOIN client cl ON cl.client_id = c.client_id "
            "ORDER BY c.compte_id"
        )
        for client_id, email, password_hash in cur.fetchall():
            rows.append((client_id, email, password_hash))
    if len(rows) < 2:
        pytest.skip(f"Need >= 2 real COMPTE rows, found {len(rows)}")
    return rows


class TestOracleConnection:
    def test_select_one(self, oracle_conn):
        with oracle_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM dual")
            assert cur.fetchone()[0] == 1

    def test_expected_tables_exist(self, oracle_conn):
        with oracle_conn.cursor() as cur:
            cur.execute("SELECT table_name FROM user_tables")
            tables = {r[0].upper() for r in cur.fetchall()}
        for t in ("COMPTE", "CLIENT", "CONTRAT", "VERSEMENT", "EPARGNE",
                  "BENEFICIAIRE"):
            assert t in tables, f"Missing HAYETT_USER table {t}"

    def test_expected_columns_exist(self, oracle_conn):
        expected = {
            "COMPTE": {"COMPTE_ID", "EMAIL", "PASSWORD_HASH", "CLIENT_ID"},
            "CLIENT": {"CLIENT_ID", "CIN", "NOM", "PRENOM", "DATE_NAISSANCE",
                       "TELEPHONE"},
            "CONTRAT": {"CONTRAT_ID", "CLIENT_ID", "DATE_SOUSCRIPTION",
                        "DUREE", "PERIODICITE", "MONTANT_PRIME", "STATUT"},
            "VERSEMENT": {"VERSEMENT_ID", "CONTRAT_ID", "DATE_VERSEMENT",
                          "MONTANT", "TYPE_VERSEMENT"},
            "EPARGNE": {"EPARGNE_ID", "CONTRAT_ID", "DATE_CALCUL",
                        "MONTANT_EPARGNE", "PARTICIPATION_BENEFICES"},
            "BENEFICIAIRE": {"BENEFICIAIRE_ID", "CONTRAT_ID", "NOM",
                             "PRENOM", "RELATION"},
        }
        for table, cols in expected.items():
            with oracle_conn.cursor() as cur:
                cur.execute("SELECT column_name FROM user_tab_columns "
                            "WHERE table_name = :t", {"t": table})
                actual = {r[0].upper() for r in cur.fetchall()}
            missing = cols - actual
            assert not missing, f"{table} missing columns: {sorted(missing)}"

    def test_verify_read_only_cannot_create_alter_drop(self, oracle_conn):
        # check_schema is strictly read-only: no DDL statements at all.
        import inspect
        src = inspect.getsource(database.check_schema)
        for banned in ("CREATE", "ALTER", "DROP", "TRUNCATE"):
            assert not any(
                line.strip().upper().startswith(banned)
                for line in src.splitlines()
                if line.strip()
            ), f"check_schema must never contain {banned}"


class TestRealAuthenticationLookup:
    def test_find_account_by_email_returns_real_client(self, accounts):
        client_id, email, _ = accounts[0]
        found = database.find_account_by_email(email)
        assert found is not None
        assert found["client_id"] == client_id

    def test_account_has_password_hash_column(self, accounts):
        _, _, password_hash = accounts[0]
        assert password_hash
        assert len(password_hash) > 0


class TestRealIsolation:
    """Two real different accounts must ONLY ever see their own data."""

    def _contrat_ids(self, client_id):
        return {r["contrat_id"] for r in database.list_contrats(client_id)}

    def test_contrats_are_disjoint(self, accounts):
        a, b = accounts[0][0], accounts[1][0]
        assert a != b
        assert self._contrat_ids(a).isdisjoint(self._contrat_ids(b))

    def test_epargne_contracts_are_disjoint(self, accounts):
        a, b = accounts[0][0], accounts[1][0]
        def ids(cid):
            return {r["contrat_id"] for r in database.latest_epargne(cid)}
        assert ids(a).isdisjoint(ids(b))

    def test_versements_contracts_are_disjoint(self, accounts):
        from datetime import date
        a, b = accounts[0][0], accounts[1][0]
        year = date.today().year
        def ids(cid):
            return {r["contrat_id"] for r in database.versements_annee(cid, year)}
        assert ids(a).isdisjoint(ids(b))

    def test_beneficiaires_contracts_are_disjoint(self, accounts):
        a, b = accounts[0][0], accounts[1][0]
        def ids(cid):
            return {r["contrat_id"] for r in database.list_beneficiaires(cid)}
        assert ids(a).isdisjoint(ids(b))

    def test_query_sql_binds_client_id(self):
        # Guards: every personal-data query is parameterised by :client_id.
        for name in ("list_contrats", "latest_epargne", "versements_annee",
                     "list_beneficiaires"):
            import inspect
            src = inspect.getsource(getattr(database, name))
            assert ":client_id" in src, f"{name} does not bind :client_id"
            assert "client_id = :client_id" in src, f"{name}"

    def test_find_client_functions_bind_parameters(self):
        # Guards: client lookups are parameterised (:client_id, :cin) and
        # never interpolate values into SQL strings.
        import inspect
        for name, param in (("find_client_by_id", ":client_id"),
                            ("find_client_by_cin", ":cin")):
            src = inspect.getsource(getattr(database, name))
            assert param in src, f"{name} does not bind {param}"


class TestNoSeedSurface:
    """SEED_DB_ON_START is OFF: normal startup inserts nothing."""

    def test_seed_flag_off_by_default(self):
        from config import SEED_DB_ON_START
        assert SEED_DB_ON_START is False

    def test_ensure_schema_not_called_when_seed_off(self, monkeypatch):
        monkeypatch.setattr(database, "SEED_DB_ON_START", False)
        called = {"ensure": 0, "seed": 0}
        monkeypatch.setattr(database, "ensure_schema",
                            lambda conn: called.__setitem__("ensure", called["ensure"] + 1))
        monkeypatch.setattr(database, "_seed_data",
                            lambda conn: called.__setitem__("seed", called["seed"] + 1))
        database.init_db()
        assert called == {"ensure": 0, "seed": 0}