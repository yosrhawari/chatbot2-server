"""Tests flux create_client_account avec email (mock Oracle + mock SMTP)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import tools.create_client_account as cca
from security import hash_password, verify_password


class FakeCursor:
    def __init__(self, conn=None, rows=None, one=None):
        self._conn = conn
        self._rows = rows
        self._one = one

    def execute(self, sql, params=None):
        if self._conn is not None:
            self._conn.executes.append((sql, params))

    def fetchall(self):
        return self._rows or []

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
        return FakeCursor(conn=self, rows=self._cursor._rows if self._cursor else None,
                          one=self._cursor._one if self._cursor else None)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _mock_inputs(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))
    # First call = password, second = confirm. For resend, only password.
    # Use side_effect via list
    pw_iter = iter(["InitialPass123!", "InitialPass123!"])
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(pw_iter))


def test_hash_stocke_est_bcrypt_pas_clair(monkeypatch, capsys):
    conn = FakeConn()
    _mock_inputs(monkeypatch, ["Ben", "Ahmed", "CIN999", "1990-01-01", "+216", "new@hayett.tn"])
    monkeypatch.setattr(cca, "ORACLE_USER", "u")
    monkeypatch.setattr(cca, "ORACLE_PASSWORD", "p")
    monkeypatch.setattr(cca, "ORACLE_DSN", "d")
    monkeypatch.setattr(cca, "_connect", lambda: conn)
    monkeypatch.setattr(cca, "_cin_exists", lambda c, cin: False)
    monkeypatch.setattr(cca, "_email_exists", lambda c, e: False)
    monkeypatch.setattr(cca, "_next_client_id", lambda c: 999)
    monkeypatch.setattr(cca, "_next_compte_id", lambda c: 888)

    # Mock email ok
    monkeypatch.setattr("email_service.is_email_configured", lambda: True)
    sent = {}
    def fake_send(to, prenom, nom, pwd):
        sent["pwd"] = pwd
        sent["to"] = to
        # Verify that password_hash in executes is bcrypt and not clear
        compte_insert = next((e for e in conn.executes if "INSERT INTO compte" in e[0]), None)
        assert compte_insert is not None
        stored_hash = compte_insert[1][2]  # [compte_id, email, password_hash, client_id]
        assert stored_hash.startswith("$2b$")
        assert pwd not in stored_hash
        assert verify_password(pwd, stored_hash) is True

    monkeypatch.setattr("email_service.send_welcome_email", fake_send)

    rc = cca.main()
    assert rc == 0
    assert sent["to"] == "new@hayett.tn"
    assert sent["pwd"] == "InitialPass123!"
    assert conn.committed is True
    out = capsys.readouterr().out
    assert "InitialPass123!" not in out


def test_email_construit_avec_bon_destinataire(monkeypatch):
    conn = FakeConn()
    _mock_inputs(monkeypatch, ["Ben", "Ahmed", "CIN1", "1990-01-01", "+216", "dest@hayett.tn"])
    monkeypatch.setattr(cca, "ORACLE_USER", "u")
    monkeypatch.setattr(cca, "ORACLE_PASSWORD", "p")
    monkeypatch.setattr(cca, "ORACLE_DSN", "d")
    monkeypatch.setattr(cca, "_connect", lambda: conn)
    monkeypatch.setattr(cca, "_cin_exists", lambda c, cin: False)
    monkeypatch.setattr(cca, "_email_exists", lambda c, e: False)
    monkeypatch.setattr(cca, "_next_client_id", lambda c: 1)
    monkeypatch.setattr(cca, "_next_compte_id", lambda c: 1)
    captured = {}
    def fake_send(to, prenom, nom, pwd):
        captured["to"] = to
        captured["prenom"] = prenom
        captured["pwd"] = pwd
    monkeypatch.setattr("email_service.is_email_configured", lambda: True)
    monkeypatch.setattr("email_service.send_welcome_email", fake_send)
    cca.main()
    assert captured["to"] == "dest@hayett.tn"
    assert captured["pwd"] == "InitialPass123!"


def test_echec_oracle_aucun_email_envoye(monkeypatch, capsys):
    import oracledb
    conn = FakeConn()
    _mock_inputs(monkeypatch, ["Ben", "Ahmed", "CIN2", "1990-01-01", "+216", "fail@hayett.tn"])
    monkeypatch.setattr(cca, "ORACLE_USER", "u")
    monkeypatch.setattr(cca, "ORACLE_PASSWORD", "p")
    monkeypatch.setattr(cca, "ORACLE_DSN", "d")

    def boom_connect():
        return conn
    monkeypatch.setattr(cca, "_connect", boom_connect)
    monkeypatch.setattr(cca, "_cin_exists", lambda c, cin: False)
    monkeypatch.setattr(cca, "_email_exists", lambda c, e: False)
    # Simulate IntegrityError on commit via next_id
    def boom_next(c):
        raise oracledb.IntegrityError("BOOM unique constraint")
    monkeypatch.setattr(cca, "_next_client_id", boom_next)

    email_called = {"v": False}
    monkeypatch.setattr("email_service.is_email_configured", lambda: True)
    monkeypatch.setattr("email_service.send_welcome_email", lambda *a, **k: email_called.__setitem__("v", True))

    rc = cca.main()
    assert rc == 1
    assert email_called["v"] is False
    assert conn.rolled_back is True
    assert "InitialPass123!" not in capsys.readouterr().out


def test_echec_smtp_apres_commit_erreur_claire(monkeypatch, capsys):
    conn = FakeConn()
    _mock_inputs(monkeypatch, ["Ben", "Ahmed", "CIN3", "1990-01-01", "+216", "ok@hayett.tn"])
    monkeypatch.setattr(cca, "ORACLE_USER", "u")
    monkeypatch.setattr(cca, "ORACLE_PASSWORD", "p")
    monkeypatch.setattr(cca, "ORACLE_DSN", "d")
    monkeypatch.setattr(cca, "_connect", lambda: conn)
    monkeypatch.setattr(cca, "_cin_exists", lambda c, cin: False)
    monkeypatch.setattr(cca, "_email_exists", lambda c, e: False)
    monkeypatch.setattr(cca, "_next_client_id", lambda c: 10)
    monkeypatch.setattr(cca, "_next_compte_id", lambda c: 20)
    monkeypatch.setattr("email_service.is_email_configured", lambda: True)

    def boom_send(*a, **k):
        raise RuntimeError("SMTP timeout")
    monkeypatch.setattr("email_service.send_welcome_email", boom_send)

    rc = cca.main()
    assert rc == 2
    assert conn.committed is True
    assert conn.rolled_back is False
    out = capsys.readouterr().out
    assert "resend_credentials" in out
    assert "MAIS l'envoi d'email a echoue" in out
    assert "InitialPass123!" not in out


def test_smtp_non_configure_retourne_2(monkeypatch, capsys):
    conn = FakeConn()
    _mock_inputs(monkeypatch, ["Ben", "Ahmed", "CIN4", "1990-01-01", "+216", "x@hayett.tn"])
    monkeypatch.setattr(cca, "ORACLE_USER", "u")
    monkeypatch.setattr(cca, "ORACLE_PASSWORD", "p")
    monkeypatch.setattr(cca, "ORACLE_DSN", "d")
    monkeypatch.setattr(cca, "_connect", lambda: conn)
    monkeypatch.setattr(cca, "_cin_exists", lambda c, cin: False)
    monkeypatch.setattr(cca, "_email_exists", lambda c, e: False)
    monkeypatch.setattr(cca, "_next_client_id", lambda c: 11)
    monkeypatch.setattr(cca, "_next_compte_id", lambda c: 21)
    monkeypatch.setattr("email_service.is_email_configured", lambda: False)

    rc = cca.main()
    assert rc == 2
    assert "Email non configure" in capsys.readouterr().out


def test_aucun_password_dans_logs(monkeypatch, caplog, capsys):
    conn = FakeConn()
    _mock_inputs(monkeypatch, ["Ben", "Ahmed", "CIN5", "1990-01-01", "+216", "log@hayett.tn"])
    monkeypatch.setattr(cca, "ORACLE_USER", "u")
    monkeypatch.setattr(cca, "ORACLE_PASSWORD", "p")
    monkeypatch.setattr(cca, "ORACLE_DSN", "d")
    monkeypatch.setattr(cca, "_connect", lambda: conn)
    monkeypatch.setattr(cca, "_cin_exists", lambda c, cin: False)
    monkeypatch.setattr(cca, "_email_exists", lambda c, e: False)
    monkeypatch.setattr(cca, "_next_client_id", lambda c: 12)
    monkeypatch.setattr(cca, "_next_compte_id", lambda c: 22)
    monkeypatch.setattr("email_service.is_email_configured", lambda: True)
    monkeypatch.setattr("email_service.send_welcome_email", lambda *a, **k: None)

    pwd = "InitialPass123!"
    with caplog.at_level("INFO"):
        cca.main()
    for rec in caplog.records:
        assert pwd not in rec.getMessage()
    assert pwd not in capsys.readouterr().out


def test_login_fonctionne_apres_creation(monkeypatch):
    """Le hash stocké permet un login via verify_password."""
    conn = FakeConn()
    _mock_inputs(monkeypatch, ["Ben", "Ahmed", "CIN6", "1990-01-01", "+216", "login@hayett.tn"])
    monkeypatch.setattr(cca, "ORACLE_USER", "u")
    monkeypatch.setattr(cca, "ORACLE_PASSWORD", "p")
    monkeypatch.setattr(cca, "ORACLE_DSN", "d")
    monkeypatch.setattr(cca, "_connect", lambda: conn)
    monkeypatch.setattr(cca, "_cin_exists", lambda c, cin: False)
    monkeypatch.setattr(cca, "_email_exists", lambda c, e: False)
    monkeypatch.setattr(cca, "_next_client_id", lambda c: 13)
    monkeypatch.setattr(cca, "_next_compte_id", lambda c: 23)
    monkeypatch.setattr("email_service.is_email_configured", lambda: True)
    monkeypatch.setattr("email_service.send_welcome_email", lambda *a, **k: None)

    rc = cca.main()
    assert rc == 0
    # Recupere le hash réellement inséré
    compte_insert = next(e for e in conn.executes if "INSERT INTO compte" in e[0])
    stored_hash = compte_insert[1][2]
    assert verify_password("InitialPass123!", stored_hash) is True
    assert verify_password("WrongPass!", stored_hash) is False

    # Simule auth.login: find_account_by_email retourne le hash, verify_password OK
    import auth as auth_module
    monkeypatch.setattr(auth_module, "find_account_by_email",
                        lambda email: {"client_id": 13, "password_hash": stored_hash, "nom": "Ben", "prenom": "Ahmed"})
    result = auth_module.login("login@hayett.tn", "InitialPass123!")
    assert result is not None
    assert result["client_id"] == 13
