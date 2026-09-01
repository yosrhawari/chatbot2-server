"""Unit tests for CIN integration (no real Oracle required).

Covers:
- database.find_client_by_cin (param binding, None when absent)
- tools: CIN is required/unique in create_client_account
- tools: _find_client_by_cin raises on missing / non-unique CIN
"""
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import database
import tools.create_client_account as cca
import tools.create_contrat as cc
import tools.create_versement as cv
import tools.create_beneficiaire as cb

# conftest.py globally replaces sys.modules["database"] with a MagicMock to
# isolate the AI modules. This test needs the REAL database module, so restore
# it (pop the mock, then re-import). Tools do not import database directly.
import sys as _sys  # noqa: E402

if _sys.modules.get("database", None) is not database or getattr(database, "__name__", "") != "database":
    _sys.modules.pop("database", None)
    import importlib  # noqa: E402

    database = importlib.import_module("database")


class FakeCursor:
    """Minimal cursor double that records execute() calls on the conn log."""

    def __init__(self, conn=None, rows=None, one=None):
        self._conn = conn
        self._rows = rows if rows is not None else []
        self._one = one

    def execute(self, sql, params=None):
        if self._conn is not None:
            self._conn.executes.append((sql, params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cursor=None):
        self.executes = []
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        # Each with-block gets a fresh cursor that logs into the shared list.
        return FakeCursor(conn=self, rows=self._cursor._rows if self._cursor else None,
                          one=self._cursor._one if self._cursor else None)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


# ----------------------------- database.find_client_by_cin -------------

def test_find_client_by_cin_returns_dict(monkeypatch):
    monkeypatch.setattr(
        database, "_fetch",
        lambda sql, params: [{"client_id": 7, "cin": "ABC", "nom": "n", "prenom": "p"}],
    )
    row = database.find_client_by_cin("ABC")
    assert row["client_id"] == 7
    assert row["cin"] == "ABC"


def test_find_client_by_cin_none_when_absent(monkeypatch):
    monkeypatch.setattr(database, "_fetch", lambda sql, params: [])
    assert database.find_client_by_cin("ZZZ") is None


def test_find_client_by_cin_binds_parameter(monkeypatch):
    captured = {}

    def fake_fetch(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(database, "_fetch", fake_fetch)
    database.find_client_by_cin("Q")
    assert ":cin" in captured["sql"]
    assert captured["params"] == {"cin": "Q"}


# ----------------------------- create_client_account CIN ---------------

def _mock_inputs(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "secret123")


def test_create_client_account_rejects_empty_cin(monkeypatch, capsys):
    _mock_inputs(monkeypatch, [
        "Ben Salah", "Ahmed", "", "1985-03-14", "+216 1", "a@b.com",
    ])
    monkeypatch.setattr(cca, "ORACLE_USER", "u")
    monkeypatch.setattr(cca, "ORACLE_PASSWORD", "p")
    monkeypatch.setattr(cca, "ORACLE_DSN", "d")

    rc = cca.main()
    assert rc == 1
    assert "CIN" in capsys.readouterr().out


def test_create_client_account_rejects_duplicate_cin(monkeypatch, capsys):
    _mock_inputs(monkeypatch, [
        "Ben Salah", "Ahmed", "CINX", "1985-03-14", "+216 1", "a@b.com",
    ])
    monkeypatch.setattr(cca, "ORACLE_USER", "u")
    monkeypatch.setattr(cca, "ORACLE_PASSWORD", "p")
    monkeypatch.setattr(cca, "ORACLE_DSN", "d")
    monkeypatch.setattr(cca, "_connect", lambda: FakeConn())
    monkeypatch.setattr(cca, "_cin_exists", lambda conn, cin: True)

    rc = cca.main()
    assert rc == 1
    assert "CIN deja existant" in capsys.readouterr().out


def test_create_client_account_inserts_cin(monkeypatch, capsys):
    conn = FakeConn()
    _mock_inputs(monkeypatch, [
        "Ben Salah", "Ahmed", "CINX", "1985-03-14", "+216 1", "a@b.com",
        "secret123", "secret123",
    ])
    monkeypatch.setattr(cca, "ORACLE_USER", "u")
    monkeypatch.setattr(cca, "ORACLE_PASSWORD", "p")
    monkeypatch.setattr(cca, "ORACLE_DSN", "d")
    monkeypatch.setattr(cca, "_connect", lambda: conn)
    monkeypatch.setattr(cca, "_cin_exists", lambda conn, cin: False)
    monkeypatch.setattr(cca, "_email_exists", lambda conn, email: False)
    monkeypatch.setattr(cca, "_next_client_id", lambda conn: 999)
    monkeypatch.setattr(cca, "_next_compte_id", lambda conn: 888)
    monkeypatch.setattr(cca, "hash_password", lambda pw: "HASHED")
    # Mock email envoi (SMTP non configuré en test → sinon rc==2)
    monkeypatch.setattr("email_service.is_email_configured", lambda: True)
    monkeypatch.setattr("email_service.send_welcome_email", lambda *a, **k: None)

    rc = cca.main()
    assert rc == 0
    assert conn.committed is True

    client_insert = next(
        (e for e in conn.executes
         if "INSERT INTO client" in e[0] and "cin" in e[0]), None
    )
    assert client_insert is not None
    # positional params: [client_id, cin, nom, prenom, date_naissance, telephone]
    assert "CINX" in client_insert[1]


# ----------------------------- tools _find_client_by_cin ---------------

@pytest.mark.parametrize("mod", [cc, cv, cb])
def test_find_client_by_cin_found(mod):
    cur = FakeCursor(rows=[(42, "nom", "prenom")])
    assert mod._find_client_by_cin(cur, "CINX") == (42, "nom", "prenom")


@pytest.mark.parametrize("mod", [cc, cv, cb])
def test_find_client_by_cin_not_found(mod):
    cur = FakeCursor(rows=[])
    with pytest.raises(ValueError):
        mod._find_client_by_cin(cur, "CINX")


@pytest.mark.parametrize("mod", [cc, cv, cb])
def test_find_client_by_cin_not_unique(mod):
    cur = FakeCursor(rows=[(1, "a", "b"), (2, "c", "d")])
    with pytest.raises(ValueError):
        mod._find_client_by_cin(cur, "CINX")
