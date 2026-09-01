import logging

logger = logging.getLogger(__name__)

import threading
import time
from collections import OrderedDict

from config import AUTH_TTL_SECONDS, MAX_SESSIONS

try:
    from database import find_account_by_email, find_client_by_id, DatabaseUnavailable
    _DB_IMPORTED = True
except Exception:  # pragma: no cover - tests may import auth standalone
    _DB_IMPORTED = False

from security import verify_password


class AuthStore:
    """Server-side binding session_id -> client_id (thread-safe, TTL, LRU).

    The session cookie already identifies the browser conversation; logging in
    binds the authenticated client_id to that same cookie server-side, so the
    /chat route can resolve who is asking WITHOUT trusting anything the client
    sends in the question text.
    """

    def __init__(self, ttl: int = AUTH_TTL_SECONDS, max_entries: int = MAX_SESSIONS):
        self._ttl = ttl
        self._max = max_entries
        self._entries: "OrderedDict[str, tuple[int, float]]" = OrderedDict()  # sid -> (client_id, expires_at)
        self._lock = threading.Lock()

    def bind(self, session_id: str, client_id, ttl: int = None) -> None:
        ttl = self._ttl if ttl is None else ttl
        if not session_id:
            return
        with self._lock:
            self._entries[session_id] = (client_id, time.time() + ttl)
            self._entries.move_to_end(session_id)
            if len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def get_client_id(self, session_id) -> int | None:
        """Resolve the authenticated client_id, or None when logged out/expired."""
        if not session_id:
            return None
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                return None
            client_id, expires_at = entry
            if expires_at < time.time():
                self._entries.pop(session_id, None)
                return None
            self._entries.move_to_end(session_id)
            return client_id

    def unbind(self, session_id) -> None:
        if not session_id:
            return
        with self._lock:
            self._entries.pop(session_id, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


auth_store = AuthStore()


def login(email: str, password: str) -> dict | None:
    """Authenticate a compte. On success returns {client_id, nom, prenom, email}.

    Passwords are never stored in clear (COMPTE.password_hash, bcrypt). Any
    DB error or missing Oracle configuration yields None (login rejected).
    """
    if not _DB_IMPORTED or not email or not password:
        return None
    try:
        account = find_account_by_email(email)
    except (DatabaseUnavailable, Exception) as e:
        logger.error(f"[AUTH] Login lookup failed: {e}")
        return None
    if not account or not verify_password(password, account.get("password_hash", "")):
        return None
    return {
        "client_id": account["client_id"],
        "nom": account.get("nom", ""),
        "prenom": account.get("prenom", ""),
        "email": email,
    }


def client_info(client_id) -> dict | None:
    """Public-safe client profile for the /me endpoint."""
    if not _DB_IMPORTED or client_id is None:
        return None
    try:
        row = find_client_by_id(client_id)
    except Exception as e:
        logger.error(f"[AUTH] Client lookup failed: {e}")
        return None
    if not row:
        return None
    return {
        "client_id": row["client_id"],
        "nom": row.get("nom", ""),
        "prenom": row.get("prenom", ""),
        "telephone": row.get("telephone", ""),
    }