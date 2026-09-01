import logging

logger = logging.getLogger(__name__)

import bcrypt


def hash_password(plain: str) -> str:
    """Hash a password with bcrypt. The stored value lives in
    COMPTE.password_hash and is never reversible or stored in clear."""
    return bcrypt.hashpw((plain or "").encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time comparison against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw((plain or "").encode("utf-8"), (hashed or "").encode("utf-8"))
    except ValueError:
        return False