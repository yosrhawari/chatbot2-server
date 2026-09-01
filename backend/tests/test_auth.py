import time

import pytest

from auth import AuthStore, login


class TestAuthStore:
    def test_bind_and_get(self):
        store = AuthStore(ttl=60, max_entries=10)
        store.bind("sid1", 1001)
        assert store.get_client_id("sid1") == 1001

    def test_unbind(self):
        store = AuthStore(ttl=60, max_entries=10)
        store.bind("sid1", 1001)
        store.unbind("sid1")
        assert store.get_client_id("sid1") is None

    def test_expired_binding(self):
        store = AuthStore(ttl=-5, max_entries=10)  # already expired
        store.bind("sid1", 1001)
        assert store.get_client_id("sid1") is None

    def test_empty_session_never_binds(self):
        store = AuthStore(ttl=60, max_entries=10)
        store.bind("", 1001)
        assert store.get_client_id("") is None

    def test_lru_eviction(self):
        store = AuthStore(ttl=60, max_entries=2)
        store.bind("a", 1)
        store.bind("b", 2)
        store.bind("c", 3)
        assert store.get_client_id("a") is None
        assert store.get_client_id("b") == 2
        assert store.get_client_id("c") == 3

    def test_refresh_moves_to_end(self):
        store = AuthStore(ttl=60, max_entries=2)
        store.bind("a", 1)
        store.bind("b", 2)
        store.get_client_id("a")  # refresh a
        store.bind("c", 3)        # evicts b
        assert store.get_client_id("b") is None
        assert store.get_client_id("a") == 1

    def test_clear(self):
        store = AuthStore(ttl=60, max_entries=10)
        store.bind("a", 1)
        store.clear()
        assert store.get_client_id("a") is None


class TestLogin:
    def test_valid_credentials(self, monkeypatch):
        from security import hash_password
        monkeypatch.setattr(
            "auth.find_account_by_email",
            lambda email: {
                "compte_id": 1,
                "client_id": 1001,
                "password_hash": hash_password("secret123"),
                "nom": "Ben Salah",
                "prenom": "Ahmed",
            },
        )
        result = login("ahmed@gmail.com", "secret123")
        assert result is not None
        assert result["client_id"] == 1001
        assert result["email"] == "ahmed@gmail.com"

    def test_wrong_password_rejected(self, monkeypatch):
        from security import hash_password
        monkeypatch.setattr(
            "auth.find_account_by_email",
            lambda email: {
                "compte_id": 1,
                "client_id": 1001,
                "password_hash": hash_password("secret123"),
                "nom": "Ben Salah",
                "prenom": "Ahmed",
            },
        )
        assert login("ahmed@gmail.com", "mauvais") is None

    def test_unknown_email_rejected(self, monkeypatch):
        monkeypatch.setattr("auth.find_account_by_email", lambda email: None)
        assert login("inconnu@gmail.com", "secret123") is None

    def test_missing_fields_rejected(self, monkeypatch):
        assert login("", "x") is None
        assert login("a@b.c", "") is None

    def test_db_error_rejected(self, monkeypatch):
        def boom(email):
            raise RuntimeError("oracle down")

        monkeypatch.setattr("auth.find_account_by_email", boom)
        assert login("ahmed@gmail.com", "secret123") is None
