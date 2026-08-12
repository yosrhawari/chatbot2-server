import logging

logger = logging.getLogger(__name__)

import re
import json
from concurrent.futures import ThreadPoolExecutor

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from analytics import analyze_query, convert_numpy, fallback_french_summary
from rag import retrieve_context, answer_rag_question, condense_query
from session import session_manager, ConversationMemory

from models import (
    llm_client,
    semantic_cache,
)


# NOTE: We deliberately do NOT use llm_client.with_structured_output() here.
# Qwen2.5:1.5b does not reliably support tool/function calling, so structured
# output raised on every call and the previous `except: return "RAG"` silently
# misrouted every ANALYTICS/HYBRID query to the RAG path.
router_prompt = ChatPromptTemplate.from_messages([
    ("system", """You are a query classifier for a business intelligence chatbot.
Classify the user's message into EXACTLY ONE label:
- RAG        : policy, warranty, contracts, procedures, textual/company documents.
- ANALYTICS  : totals, averages, sums, counts, top-N, rankings, calculations,
               specific record lookups (by ID, email, name, or text field).
- HYBRID     : requires BOTH data figures AND document/policy context.

Reply with a SINGLE WORD, uppercase, no punctuation, no explanation:
RAG
or
ANALYTICS
or
HYBRID"""),
    ("user", "{query}"),
])

router_chain = router_prompt | llm_client | StrOutputParser()

_VALID_ROUTES = {"RAG", "ANALYTICS", "HYBRID"}


def classify_query(query: str) -> str:
    """Return one of RAG / ANALYTICS / HYBRID. Falls back to RAG on error."""
    try:
        raw = router_chain.invoke({"query": query}).upper()
        # Small models often wrap the label in prose or punctuation despite
        # instructions. Extract the first valid label token from the response.
        for token in re.findall(r"[A-Z]+", raw):
            if token in _VALID_ROUTES:
                return token
        logger.warning(f"[ROUTER] Unrecognized output {raw!r}, defaulting to RAG")
        return "RAG"
    except Exception as e:
        logger.error(f"[ROUTER ERROR] {e}")
        return "RAG"



def _looks_like_bad_user_facing_answer(text: str) -> bool:
    """Detect raw JSON dumps or prompt echoes that must not reach the UI."""
    if not text or not str(text).strip():
        return True
    s = str(text).strip()
    low = s.lower()
    markers = (
        "résultat (json)",
        "resultat (json)",
        "analytics data",
        "données analytiques (ne pas",
        "donnees analytiques (ne pas",
        "documents context",
        "contexte documents:",
        "conversation history:",
    )
    if any(m in low for m in markers):
        return True
    # Raw JSON dump
    if s[0] in "{[":
        return True
    # Dense JSON key pattern typical of record dumps
    if s.count('":') >= 3 or s.count("':") >= 3:
        return True
    if low.startswith("```") and ("{" in s or "[" in s):
        return True
    # Many JSON-looking object openers in one answer
    if s.count('{"') >= 2 or s.count("{'") >= 2:
        return True
    return False


hybrid_prompt = ChatPromptTemplate.from_messages([
    ("system", """Tu es un assistant commercial d'entreprise. Combine les DEUX sources (Analytique et Documents).

FORMAT:
- Sois concis. Pas de longs paragraphes d'introduction ou de conclusion.
- Si l'utilisateur demande plusieurs choses, traite CHAQUE point sous un court titre en gras, suivi de puces détaillées.
- Place les chiffres concrets (totaux, noms, montants, pourcentages) directement dans les puces.
- N'affiche JAMAIS de JSON brut, de listes d'objets {{ }}, ni de blocs de code. Reformule toujours en prose/puces françaises.

ANCRAGE (très important):
- Utilise UNIQUEMENT les Données Analytiques pour tout nombre, total, classement, nom ou montant. N'invente JAMAIS.
- Si un chiffre demandé est absent des Données Analytiques, dis brièvement qu'il n'est pas disponible — ne le fabrique pas."""),
    ("user",
     "Données analytiques (ne pas coller ce bloc brut dans la réponse):\n{analytics_context}\n\n"
     "Contexte documents:\n{rag_context}\n\n"
     "Historique:\n{history}\n\n"
     "Question:\n{question}\n\n"
     "Rédige la réponse finale en français, sans JSON."),
])

hybrid_chain = hybrid_prompt | llm_client | StrOutputParser()


def handle_analytics(query: str, memory: ConversationMemory) -> dict:
    result = analyze_query(query)
    answer = result.get("answer", "Aucune réponse trouvée.")

    memory.add_user(query)
    memory.add_assistant(answer)

    return {"type": "analytics", "answer": answer, "data": result.get("data")}


def handle_rag(query: str, memory: ConversationMemory, condensed_query: str = None) -> dict:
    result = answer_rag_question(query, memory, condensed_query=condensed_query)
    return {
        "type": "rag",
        "answer": result["answer"],
        "sources": result["sources"],
        # Surface the condensed query so the router can key the cache by it.
        "condensed_query": result["condensed_query"],
    }


def handle_hybrid(query: str, memory: ConversationMemory) -> dict:
    history = memory.get_history()

    with ThreadPoolExecutor(max_workers=2) as executor:
        # compose=False: skip the analytics composer LLM call entirely. The
        # hybrid chain writes the final prose anyway, so composing here would
        # be a wasted (and latency-adding) generation.
        future_analytics = executor.submit(analyze_query, query, False)
        future_rag = executor.submit(retrieve_context, query, history)
        analytics_result = future_analytics.result()
        retrieval_result = future_rag.result()

    # Pass the RAW structured figures (analytics_result["data"]) to the hybrid
    # model instead of pre-composed French prose. The prompt asks it to ground
    # every number on the "Analytics Data", so it must see the actual values --
    # feeding it prose loses precision and invites the small model to garble
    # figures. Serialise + cap to protect the small context window.
    analytics_data = analytics_result.get("data", [])
    analytics_context = json.dumps(
        convert_numpy(analytics_data), ensure_ascii=False, default=str
    )
    if len(analytics_context) > 4000:
        analytics_context = analytics_context[:4000] + " ...(truncated)"

    rag_context = retrieval_result.get("context", "")

    answer = hybrid_chain.invoke({
        "question": query,
        "analytics_context": analytics_context,
        "rag_context": rag_context,
        "history": history,
    }).strip()

    # Small models sometimes dump the analytics JSON or echo the prompt.
    # Prefer a clean French fallback built from the analytics result (no 2nd LLM).
    if not answer or _looks_like_bad_user_facing_answer(answer):
        explanation = analytics_result.get("explanation") or ""
        if not explanation:
            explanation = analytics_result.get("answer") or ""
        answer = fallback_french_summary(
            query,
            explanation if isinstance(explanation, str) else "",
            analytics_result.get("data", []),
        )
        if rag_context and answer:
            answer = (
                answer.rstrip()
                + "\n\n**Contexte documentaire**\n"
                + "Des éléments de documentation ont également été pris en compte."
            )

    memory.add_user(query)
    memory.add_assistant(answer)

    return {
        "type": "hybrid",
        "answer": answer,
        "analytics": analytics_result,
        # Cite only the docs that actually fit into the RAG context.
        "sources": [
            doc.metadata.get("source")
            for doc in retrieval_result.get("used_documents", retrieval_result["documents"])
            if doc.metadata.get("source")
        ],
    }


def route_query(query: str, session_id: str) -> dict:
    """Route a query for a specific session.

    `session_id` is REQUIRED. Callers must pass a stable per-user/per-chat id.
    The web layer (app.py) mints one from the session cookie, so this should
    never be missing in practice. We refuse to fall back to a global 'default'
    bucket because that caused cross-user memory bleed.
    """
    if not session_id:
        raise ValueError("session_id is required")

    memory = session_manager.get_memory(session_id)
    query_type = classify_query(query)
    logger.info(f"[ROUTER] {query_type}")

    if query_type == "ANALYTICS":
        return handle_analytics(query, memory)

    if query_type == "HYBRID":
        return handle_hybrid(query, memory)

    # RAG route: safe to cache (shared documentation knowledge).
    # Key the cache by the CONDENSED (standalone, history-independent) query,
    # NOT the raw follow-up. A follow-up like "et son prix?" only makes sense
    # against a specific history, so caching its answer under the raw text and
    # serving it to another session would be wrong. The condensed query is
    # self-contained and safe to share. We condense ONCE here and reuse it in
    # handle_rag so there is no second condensation LLM call.
    history = memory.get_history()
    condensed = condense_query(query, history)

    cached = semantic_cache.get_cached_response(condensed)
    if cached is not None:
        memory.add_user(query)
        memory.add_assistant(cached.get("answer", ""))
        return cached

    response = handle_rag(query, memory, condensed_query=condensed)
    semantic_cache.add_to_cache(response.get("condensed_query", condensed), response)
    return response
