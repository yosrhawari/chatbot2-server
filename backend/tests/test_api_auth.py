import sys
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

# rag.py instantiates the Chroma vector store at import time; under mocked
# `models` that fails pydantic validation. Stub the module like conftest does.
_rag_stub = MagicMock()
_rag_stub.retrieve_context = MagicMock(return_value={"context": "", "documents": []})
_rag_stub.answer_rag_question = MagicMock()
_rag_stub.condense_query = MagicMock(side_effect=lambda q, history="": q.strip())
_rag_stub.extract_articles = MagicMock(return_value=[])
_rag_stub._no_answer = MagicMock(return_value="pas de réponse")
sys.modules["rag"] = _rag_stub

from app import app  # noqa: E402


class TestAuthEndpoints:
    def test_login_success_binds_session(self, monkeypatch):
        monkeypatch.setattr("app.db_available", lambda: True)
        monkeypatch.setattr(
            "app.auth_login",
            lambda email, pwd: {"client_id": 1001, "nom": "Ben Salah",
                                "prenom": "Ahmed", "email": email},
        )
        with TestClient(app) as client:
            resp = client.post("/login", json={"email": "ahmed@gmail.com", "password": "secret123"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["client_id"] == 1001
            assert "session_id" in resp.cookies

    def test_login_bad_credentials_401(self, monkeypatch):
        monkeypatch.setattr("app.db_available", lambda: True)
        monkeypatch.setattr("app.auth_login", lambda email, pwd: None)
        with TestClient(app) as client:
            resp = client.post("/login", json={"email": "ahmed@gmail.com", "password": "wrong"})
            assert resp.status_code == 401

    def test_login_when_database_down_503(self, monkeypatch):
        monkeypatch.setattr("app.db_available", lambda: False)
        with TestClient(app) as client:
            resp = client.post("/login", json={"email": "a@b.c", "password": "x"})
            assert resp.status_code == 503

    def test_me_requires_authentication(self):
        with TestClient(app) as client:
            resp = client.get("/me")
            assert resp.status_code == 401

    def test_me_returns_client_after_login(self, monkeypatch):
        monkeypatch.setattr("app.db_available", lambda: True)
        monkeypatch.setattr(
            "app.auth_login",
            lambda email, pwd: {"client_id": 1001, "nom": "Ben Salah",
                                "prenom": "Ahmed", "email": email},
        )
        monkeypatch.setattr(
            "app.client_info",
            lambda cid: {"client_id": 1001, "nom": "Ben Salah", "prenom": "Ahmed",
                         "telephone": "+216 98 123 456"},
        )
        with TestClient(app) as client:
            client.post("/login", json={"email": "ahmed@gmail.com", "password": "secret123"})
            resp = client.get("/me")
            assert resp.status_code == 200
            assert resp.json()["client_id"] == 1001

    def test_logout_clears_session(self, monkeypatch):
        monkeypatch.setattr("app.db_available", lambda: True)
        monkeypatch.setattr(
            "app.auth_login",
            lambda email, pwd: {"client_id": 1001, "nom": "Ben Salah",
                                "prenom": "Ahmed", "email": email},
        )
        with TestClient(app) as client:
            client.post("/login", json={"email": "ahmed@gmail.com", "password": "secret123"})
            resp = client.post("/logout")
            assert resp.status_code == 200
            assert client.get("/me").status_code == 401

    def test_health_reports_database_state(self, monkeypatch):
        monkeypatch.setattr("app.db_available", lambda: True)
        with TestClient(app) as client:
            body = client.get("/health").json()
            assert body["status"] == "ok"
            assert body["database"] == "oracle"