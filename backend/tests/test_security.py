from security import hash_password, verify_password


class TestHashPassword:
    def test_hash_and_verify_roundtrip(self):
        hashed = hash_password("secret123")
        assert hashed != "secret123"
        assert verify_password("secret123", hashed) is True

    def test_wrong_password_rejected(self):
        hashed = hash_password("secret123")
        assert verify_password("secret456", hashed) is False

    def test_empty_password(self):
        hashed = hash_password("")
        assert verify_password("", hashed) is True

    def test_invalid_hash_rejected(self):
        assert verify_password("secret123", "not-a-bcrypt-hash") is False

    def test_hashes_are_salted(self):
        assert hash_password("same") != hash_password("same")

    def test_password_not_stored_in_clear(self):
        hashed = hash_password("mon-mot-de-passe")
        assert "mon-mot-de-passe" not in hashed
