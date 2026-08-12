import threading
from session import ConversationMemory, SessionManager


class TestConversationMemory:
    def test_add_and_get_single_turn(self):
        mem = ConversationMemory(max_turns=5)
        mem.add_user("Bonjour")
        mem.add_assistant("Bonjour, comment puis-je vous aider?")
        history = mem.get_history()
        assert "User: Bonjour" in history
        assert "Assistant: Bonjour, comment puis-je vous aider?" in history

    def test_empty_history(self):
        mem = ConversationMemory(max_turns=5)
        assert mem.get_history() == ""

    def test_clear(self):
        mem = ConversationMemory(max_turns=5)
        mem.add_user("test")
        mem.add_assistant("response")
        mem.clear()
        assert mem.get_history() == ""

    def test_max_turns_rolling(self):
        mem = ConversationMemory(max_turns=2)
        mem.add_user("msg1")
        mem.add_assistant("resp1")
        mem.add_user("msg2")
        mem.add_assistant("resp2")
        mem.add_user("msg3")
        mem.add_assistant("resp3")

        history = mem.get_history()
        assert "msg1" not in history
        assert "resp1" not in history
        assert "msg2" in history
        assert "msg3" in history

    def test_strips_whitespace(self):
        mem = ConversationMemory(max_turns=5)
        mem.add_user("  hello  ")
        mem.add_assistant("  world  ")
        assert "  hello  " not in mem.get_history()
        assert "hello" in mem.get_history()
        assert "world" in mem.get_history()

    def test_empty_message(self):
        mem = ConversationMemory(max_turns=5)
        mem.add_user("")
        mem.add_assistant("")
        history = mem.get_history()
        assert "User:" in history
        assert "Assistant:" in history

    def test_thread_safety(self):
        mem = ConversationMemory(max_turns=200)
        errors = []

        def writer(prefix, count):
            try:
                for i in range(count):
                    mem.add_user(f"{prefix}_user_{i}")
                    mem.add_assistant(f"{prefix}_assistant_{i}")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=("A", 50)),
            threading.Thread(target=writer, args=("B", 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        history = mem.get_history()
        assert "A_user_0" in history
        assert "B_user_0" in history

    def test_max_turns_at_least_one(self):
        mem = ConversationMemory(max_turns=0)
        mem.add_user("test")
        mem.add_assistant("resp")
        assert mem.get_history() != ""


class TestSessionManager:
    def test_get_memory_creates_new(self):
        mgr = SessionManager(max_sessions=10)
        mem = mgr.get_memory("session_1")
        assert mem is not None
        assert isinstance(mem, ConversationMemory)

    def test_get_memory_reuses_existing(self):
        mgr = SessionManager(max_sessions=10)
        mem1 = mgr.get_memory("session_1")
        mem2 = mgr.get_memory("session_1")
        assert mem1 is mem2

    def test_default_session(self):
        mgr = SessionManager(max_sessions=10)
        mem1 = mgr.get_memory()
        mem2 = mgr.get_memory()
        assert mem1 is mem2

    def test_reset_removes_session(self):
        mgr = SessionManager(max_sessions=10)
        mem1 = mgr.get_memory("session_1")
        mgr.reset("session_1")
        mem2 = mgr.get_memory("session_1")
        assert mem1 is not mem2

    def test_reset_default_session(self):
        mgr = SessionManager(max_sessions=10)
        mem1 = mgr.get_memory()
        mgr.reset(None)
        mem2 = mgr.get_memory()
        assert mem1 is not mem2

    def test_lru_eviction(self):
        mgr = SessionManager(max_sessions=3)
        s1 = mgr.get_memory("s1")
        s2 = mgr.get_memory("s2")
        s3 = mgr.get_memory("s3")

        # Access s1 to make it recently used
        mgr.get_memory("s1")

        # Add a 4th session — should evict s2 (least recently used)
        s4 = mgr.get_memory("s4")

        # s2 should be evicted (new session created)
        s2_new = mgr.get_memory("s2")
        assert s2_new is not s2

        # s1 should still be the same (was accessed)
        assert mgr.get_memory("s1") is s1

    def test_different_sessions_isolated(self):
        mgr = SessionManager(max_sessions=10)
        mem_a = mgr.get_memory("A")
        mem_b = mgr.get_memory("B")

        mem_a.add_user("hello from A")
        mem_b.add_user("hello from B")

        assert "hello from A" in mem_a.get_history()
        assert "hello from A" not in mem_b.get_history()
        assert "hello from B" in mem_b.get_history()
        assert "hello from B" not in mem_a.get_history()
