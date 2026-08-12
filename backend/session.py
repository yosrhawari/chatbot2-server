import threading
from collections import deque, OrderedDict

from config import MEMORY_SIZE, MAX_SESSIONS

# Character budget for the history string returned by get_history().
# A turn cap alone is not enough: a single very long user paste or assistant
# answer can blow past the small model's context window even within the turn
# limit. We therefore ALSO cap the rendered history by characters.
try:
    from config import HISTORY_MAX_CHARS
except ImportError:
    HISTORY_MAX_CHARS = 2000


class ConversationMemory:
    """Thread-safe rolling conversation history for a single session."""

    def __init__(self, max_turns: int = MEMORY_SIZE, max_chars: int = HISTORY_MAX_CHARS):
        # Each turn is a (user, assistant) pair -> store 2 messages per turn.
        self._messages = deque(maxlen=max(1, max_turns) * 2)
        self._max_chars = max_chars
        self._lock = threading.Lock()

    def add_user(self, text: str) -> None:
        with self._lock:
            self._messages.append(("User", (text or "").strip()))

    def add_assistant(self, text: str) -> None:
        with self._lock:
            self._messages.append(("Assistant", (text or "").strip()))

    def get_history(self, max_chars: int = None) -> str:
        """Render the conversation history, capped by BOTH turns and characters.

        We walk the messages newest-first, keeping the most recent ones that
        fit inside the character budget, then restore chronological order. If
        even the single most recent message exceeds the budget we return a
        truncated tail of it so an oversized turn can never overflow the prompt.
        """
        limit = self._max_chars if max_chars is None else max_chars
        with self._lock:
            msgs = list(self._messages)

        if not msgs:
            return ""
        if limit is None or limit <= 0:
            return "\n".join(f"{role}: {content}" for role, content in msgs)

        selected: list[str] = []
        total = 0
        for role, content in reversed(msgs):
            line = f"{role}: {content}"
            # Always keep at least the most recent message; stop once adding
            # another would exceed the budget.
            if selected and total + len(line) + 1 > limit:
                break
            selected.append(line)
            total += len(line) + 1

        selected.reverse()
        text = "\n".join(selected)
        if len(text) > limit:
            # The most recent message alone is larger than the budget.
            text = "...\n" + text[-limit:]
        return text

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()


class SessionManager:
    """Creates and stores one ConversationMemory per session id (LRU bounded)."""

    def __init__(self, max_sessions: int = MAX_SESSIONS):
        self._sessions: "OrderedDict[str, ConversationMemory]" = OrderedDict()
        self._lock = threading.Lock()
        self._max_sessions = max_sessions

    def get_memory(self, session_id: str = None) -> ConversationMemory:
        sid = session_id or "default"
        with self._lock:
            mem = self._sessions.get(sid)
            if mem is not None:
                self._sessions.move_to_end(sid)
                return mem

            mem = ConversationMemory()
            self._sessions[sid] = mem
            if len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
            return mem

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id or "default", None)

session_manager = SessionManager()
