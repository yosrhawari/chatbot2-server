import sys
from unittest.mock import MagicMock

import pytest

# rag.py instantiates the Chroma vector store and the compression retriever at
# import time; under the mocked `models` module that fails pydantic validation.
# Router tests stub the whole rag module (same pattern as conftest stubs models).
_rag_stub = MagicMock()
_rag_stub.retrieve_context = MagicMock(return_value={"context": "", "documents": []})
_rag_stub.answer_rag_question = MagicMock()
_rag_stub.condense_query = MagicMock(side_effect=lambda q, history="": q.strip())
_rag_stub.extract_articles = MagicMock(return_value=[])
_rag_stub._no_answer = MagicMock(return_value="pas de réponse")
sys.modules["rag"] = _rag_stub

import router


def _memory(session_id="s1"):
    return router.session_manager.get_memory(session_id)


@pytest.fixture(autouse=True)
def _reset_semantic_cache(monkeypatch):
    # The mocked semantic_cache would return a truthy MagicMock on any lookup.
    monkeypatch.setattr(router.semantic_cache, "get_cached_response", lambda q: None)
    monkeypatch.setattr(router.semantic_cache, "add_to_cache", lambda *a, **k: None)
    router.session_manager.reset("s1")
    router.session_manager.reset("s2")
    yield
    router.session_manager.reset("s1")
    router.session_manager.reset("s2")


class TestLoginRequiredSecurity:
    """Plan §22 — a non-authenticated user never reaches personal data."""

    def test_analytics_without_login_is_refused(self, monkeypatch):
        monkeypatch.setattr(router, "classify_query", lambda q: "ANALYTICS")
        result = router.route_query("Quelle est mon épargne ?", "s1")
        assert result["type"] == "login_required"
        assert result["requires_login"] is True
        assert "connecter" in result["answer"].lower()

    def test_analytics_without_login_refused_in_arabic(self, monkeypatch):
        monkeypatch.setattr(router, "classify_query", lambda q: "ANALYTICS")
        result = router.route_query("ما هو ادخاري؟", "s1")
        assert result["type"] == "login_required"
        assert "تسجيل الدخول" in result["answer"]

    def test_hybrid_without_login_is_refused(self, monkeypatch):
        monkeypatch.setattr(router, "classify_query", lambda q: "HYBRID")
        result = router.route_query("Puis-je faire un retrait partiel ?", "s1")
        assert result["type"] == "login_required"
        assert result["requires_login"] is True

    def test_personal_analytics_never_reached_without_login(self, monkeypatch):
        monkeypatch.setattr(router, "classify_query", lambda q: "ANALYTICS")
        called = {"n": 0}
        monkeypatch.setattr(router, "handle_personal_analytics",
                            lambda *a, **k: called.__setitem__("n", called["n"] + 1))
        router.route_query("Quelle est mon épargne ?", "s1")
        assert called["n"] == 0

    def test_rag_is_public(self, monkeypatch):
        monkeypatch.setattr(router, "classify_query", lambda q: "RAG")
        fixed = {"type": "rag", "answer": "selon l'article...", "sources": []}
        monkeypatch.setattr(router, "handle_rag", lambda *a, **k: dict(fixed))
        result = router.route_query("Quelles sont les conditions du retrait partiel ?", "s1")
        assert result["type"] == "rag"
        assert result["answer"] == fixed["answer"]


class TestAuthenticatedPaths:
    def test_analytics_uses_personal_path_when_connected(self, monkeypatch):
        monkeypatch.setattr(router, "classify_query", lambda q: "ANALYTICS")
        monkeypatch.setattr(router, "classify_personal_intent", lambda q: "epargne")
        seen = {}
        def fake_personal(query, client_id, memory):
            seen["client_id"] = client_id
            return {"type": "analytics", "answer": "épargne: 18500", "data": [], "intent": "epargne"}
        monkeypatch.setattr(router, "handle_personal_analytics", fake_personal)
        result = router.route_query("Quelle est mon épargne ?", "s1", client_id=1001)
        assert seen["client_id"] == 1001
        assert result["answer"] == "épargne: 18500"

    def test_analytics_falls_back_to_pandas_for_company_questions(self, monkeypatch):
        monkeypatch.setattr(router, "classify_query", lambda q: "ANALYTICS")
        monkeypatch.setattr(router, "classify_personal_intent", lambda q: "unknown")
        called = {"n": 0}
        def fake_pandas(query, memory):
            called["n"] += 1
            return {"type": "analytics", "answer": "ventes", "data": []}
        monkeypatch.setattr(router, "handle_analytics", fake_pandas)
        result = router.route_query("Montant des ventes du mois ?", "s1", client_id=1001)
        assert called["n"] == 1
        assert result["type"] == "analytics"

    def test_db_failure_gives_graceful_error(self, monkeypatch):
        monkeypatch.setattr(router, "classify_query", lambda q: "ANALYTICS")
        monkeypatch.setattr(router, "classify_personal_intent", lambda q: "epargne")
        def boom(query, client_id, memory):
            raise RuntimeError("oracle down")
        monkeypatch.setattr(router, "handle_personal_analytics", boom)
        result = router.route_query("Quelle est mon épargne ?", "s1", client_id=1001)
        assert result["type"] == "error"
        assert "indisponible" in result["answer"]

    def test_hybrid_combines_rag_and_personal_data(self, monkeypatch):
        monkeypatch.setattr(router, "classify_query", lambda q: "HYBRID")
        monkeypatch.setattr(router, "classify_personal_intent", lambda q: "retrait")
        monkeypatch.setattr(
            router,
            "run_personal_analytics",
            lambda q, cid: {
                "intent": "retrait",
                "data": [],
                "calculation": {"max_75_pourcent": 13875.0},
                "answer": "borne max 13875",
                "language": "fr",
            },
        )
        monkeypatch.setattr(
            router,
            "retrieve_context",
            lambda q, h, condensed_query=None: {
                "context": "règles de l'article 10",
                "documents": [],
                "used_documents": [],
            },
        )
        # Generation failure → deterministic fallback must still answer.
        def fail_generation(lang):
            raise RuntimeError("llm down")
        monkeypatch.setattr(router, "_make_hybrid_chain", fail_generation)
        result = router.route_query("Puis-je retirer 10 000 DT ?", "s1", client_id=1001)
        assert result["type"] == "hybrid"
        assert "13875" in result["answer"]
        assert result["requires_login"] is False

    def test_hybrid_euro_answer_falls_back_to_deterministic(self, monkeypatch):
        # If the hybrid composer writes an amount in euros, the answer is
        # rejected and replaced by the deterministic DT fallback.
        monkeypatch.setattr(router, "classify_query", lambda q: "HYBRID")
        monkeypatch.setattr(
            router,
            "run_personal_analytics",
            lambda q, cid: {
                "intent": "retrait",
                "data": [{"epargne": 18500.0}],
                "calculation": {"max_75_pourcent": 13875.0},
                "answer": "Borne max : 13 875 DT.",
                "language": "fr",
            },
        )
        monkeypatch.setattr(
            router,
            "retrieve_context",
            lambda q, h, condensed_query=None: {
                "context": "règles de l'article 10",
                "documents": [],
                "used_documents": [],
            },
        )

        class FakeChain:
            def invoke(self, inputs):
                return "Vous pouvez retirer 1 500 €."

        monkeypatch.setattr(router, "_make_hybrid_chain", lambda lang: FakeChain())
        result = router.route_query("Puis-je retirer 10 000 € ?", "s1", client_id=1001)
        assert result["type"] == "hybrid"
        assert "€" not in result["answer"]
        assert "13 875 DT" in result["answer"]


class TestDeterministicRouting:
    """Marker-based routing, no LLM, no bare topic-word decision.
    personal only -> ANALYTICS; documentary only -> RAG;
    personal + documentary -> HYBRID; no marker -> LLM fallback."""

    @pytest.mark.parametrize(
        "question, expected",
        [
            ("Quelles sont les conditions du retrait partiel ?", "RAG"),
            ("Combien ai-je versé en 2025 ?", "ANALYTICS"),
            ("Quelle est mon épargne actuelle ?", "ANALYTICS"),
            ("Puis-je effectuer un retrait avec mon contrat actuel ?", "HYBRID"),
            ("Quel est mon montant d'épargne et quelles sont les conditions du retrait ?", "HYBRID"),
            ("Quelles sont les conditions générales du contrat ?", "RAG"),
            ("Comment fonctionne le retrait partiel ?", "RAG"),
            ("Comment consulter mon épargne ?", "ANALYTICS"),
            ("Puis-je retirer mon épargne ?", "ANALYTICS"),
            ("كم دفعت في سنة 2025؟", "ANALYTICS"),
            ("ما هي قيمة قسطي؟", "ANALYTICS"),
            ("كم تبلغ مدخراتي الحالية؟", "ANALYTICS"),
            # Anti false-positive: a general "how much does the client pay"
            # question without a personal marker stays documentary.
            ("كم يدفع العميل حسب الشروط العامة؟", "RAG"),
            # Word-boundary matching: possessives must not match inside words.
            ("Montant de mon contrat selon le contrat ?", "HYBRID"),
            ("Le panorama des conditions générales ?", "RAG"),
            ("Quelle est la définition du retrait partiel ?", "RAG"),
            ("Le panorama des garanties ?", None),
        ],
    )
    def test_marker_routing(self, question, expected):
        assert router._classify_deterministic(question) == expected

    def test_arabic_prefixed_pronouns_still_match(self):
        """Arabic attaches conjunctions/prepositions to the word (وعقدي):
        substring semantics must be preserved for Arabic tokens."""
        assert router._classify_deterministic("وعقدي ما هي الشروط؟") == "HYBRID"
        assert router._classify_deterministic("بمدخراتي كم باقٍ؟") == "ANALYTICS"

    def test_marker_routing_never_uses_llm(self, monkeypatch):
        class FakeRouter:
            def invoke(self, **kwargs):
                raise AssertionError("LLM router must not be called")

        monkeypatch.setattr(router, "router_chain", FakeRouter())
        for question in (
            "Quelles sont les conditions du retrait partiel ?",
            "Combien ai-je versé en 2025 ?",
            "Quelle est mon épargne actuelle ?",
            "Puis-je effectuer un retrait avec mon contrat actuel ?",
            "Quelles sont les conditions générales du contrat ?",
            "كم دفعت في سنة 2025؟",
        ):
            route = router.classify_query(question)
            assert route in ("RAG", "ANALYTICS", "HYBRID")

    def test_no_marker_falls_back_to_llm(self, monkeypatch):
        called = {"n": 0}

        class FakeRouter:
            def invoke(self, *args, **kwargs):
                called["n"] += 1
                return "ANALYTICS"

        monkeypatch.setattr(router, "router_chain", FakeRouter())
        result = router.classify_query("Comment ça va aujourd'hui ?")
        assert result == "ANALYTICS"
        assert called["n"] == 1

    def test_versements_2025_routed_personal_not_rag(self, monkeypatch):
        monkeypatch.setattr(router, "classify_query", lambda q: "ANALYTICS")
        monkeypatch.setattr(router, "classify_personal_intent", lambda q: "versements_annee")
        seen = {}

        def fake_personal(query, client_id, memory):
            seen["client_id"] = client_id
            return {"type": "analytics", "answer": "total 1500", "data": [],
                    "intent": "versements_annee", "requires_login": False}

        monkeypatch.setattr(router, "handle_personal_analytics", fake_personal)
        result = router.route_query("Combien ai-je versé en 2025 ?", "s1", client_id=1001)
        assert seen["client_id"] == 1001
        assert result["type"] == "analytics"


class TestNoInventedAmounts:
    """A personal-analytics question can NEVER produce an invented amount
    (e.g. 12000): not when Oracle is empty, not when Oracle is down."""

    def test_db_failure_never_yields_amount(self, monkeypatch):
        monkeypatch.setattr(router, "classify_query", lambda q: "ANALYTICS")

        def boom(query, client_id, memory):
            raise RuntimeError("oracle down")

        monkeypatch.setattr(router, "handle_personal_analytics", boom)
        result = router.route_query("Combien ai-je versé cette année ?", "s1", client_id=1001)
        assert result["type"] == "error"
        assert "indisponible" in result["answer"]
        for token in ("12000", "12 000"):
            assert token not in result["answer"]
