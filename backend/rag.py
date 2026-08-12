import logging

logger = logging.getLogger(__name__)

from typing import List
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_classic.retrievers import ContextualCompressionRetriever

from config import (
    VECTOR_SEARCH_TOP_K,
    RERANKER_TOP_K,
    MAX_CONTEXT_CHARS,
)

from ingest import get_vectorstore
from session import ConversationMemory

from models import (
    llm_client,
    reranker_compressor,
)

NO_ANSWER = (
    "Je suis désolé, mais la documentation ne contient pas les informations "
    "nécessaires pour répondre à cette question."
)

reranker_compressor.top_n = RERANKER_TOP_K

vectorstore = get_vectorstore()
base_retriever = vectorstore.as_retriever(search_kwargs={"k": VECTOR_SEARCH_TOP_K})

compression_retriever = ContextualCompressionRetriever(
    base_compressor=reranker_compressor,
    base_retriever=base_retriever,
)

# ── Prompt budget constants ───────────────────────────────────────────────────
# Qwen 1.5B has a small context window (~4 K–8 K tokens).
# Keep system prompts short and cap history / context passed in at runtime.
_MAX_HISTORY_CHARS = 800   # ~200 tokens of conversation history
_MAX_CONTEXT_CHARS = min(MAX_CONTEXT_CHARS, 3000)  # hard cap for this model

# ── History-Aware Query Condensation ─────────────────────────────────────────
# Simplified: fewer rules, shorter system message.
condense_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "Rewrite the follow-up question as a standalone search query. "
     "Replace pronouns with the real entities from the history. "
     "Return ONLY the rewritten query, nothing else."),
    ("user", "History:\n{history}\n\nFollow-up: {query}"),
])
query_condenser = condense_prompt | llm_client | StrOutputParser()

# ── QA Answering Chain ────────────────────────────────────────────────────────
# Condensed to the four rules that matter most for a small model:
# grounding, no hallucination, source citation, French output.
qa_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "Tu es un assistant commercial. Règles strictes:\n"
     "1. Réponds TOUJOURS en français, même si la question est en anglais.\n"
     "2. Réponds UNIQUEMENT avec les informations du contexte fourni.\n"
     "3. Si la réponse est absente du contexte, réponds exactement: "
     "\"Je suis désolé, mais la documentation ne contient pas les informations "
     "nécessaires pour répondre à cette question.\"\n"
     "4. Cite la source entre crochets après chaque fait, ex: [document.pdf].\n"
     "5. Sois concis et utilise des puces.\n"
     "6. N'affiche jamais de JSON brut ni de blocs de code."),
    ("user",
     "Historique:\n{history}\n\nContexte:\n{context}\n\n"
     "Question: {question}\n\n"
     "Rédige la réponse en français."),
])
qa_chain = qa_prompt | llm_client | StrOutputParser()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trim_history(history: str) -> str:
    """Keep only the tail of the history that fits in the budget."""
    if len(history) <= _MAX_HISTORY_CHARS:
        return history
    return "...\n" + history[-_MAX_HISTORY_CHARS:]


def _normalize_source(source) -> str:
    """Reduce a source metadata value to a bare filename for citations.

    Chunks may carry either a full path or a filename in metadata['source'].
    Normalising to the basename keeps citations consistent (the QA prompt asks
    the model to cite `[document.pdf]`) and makes the context labels match the
    values returned by extract_sources exactly.
    """
    if not source:
        return "unknown"
    return Path(str(source)).name


def condense_query(query: str, history: str = "") -> str:
    """Rewrite a follow-up into a standalone, history-independent query.

    Exposed at module level so the router can key the semantic cache by the
    CONDENSED query (which is safe to share across sessions) instead of the raw
    follow-up text (whose meaning depends on the caller's history). Returns the
    raw query unchanged when there is no history or on any failure.
    """
    trimmed_history = _trim_history(history)
    if not trimmed_history.strip():
        return query.strip()
    try:
        condensed = query_condenser.invoke(
            {"query": query, "history": trimmed_history}
        ).strip()
        # Fallback: if the model echoes back something too long or empty, use raw query.
        if not condensed or len(condensed) > 400:
            return query.strip()
        return condensed
    except Exception as e:
        logger.warning(f"[RAG] Query condensation failed, using raw query: {e}")
        return query.strip()


def retrieve_documents(query: str) -> List:
    return compression_retriever.invoke(query)


def _build_context(reranked_docs):
    """Concatenate chunks up to a character budget.

    Returns (context_string, used_docs) where used_docs are ONLY the documents
    that actually fit inside the budget. Callers must cite from used_docs, not
    the full reranked list -- otherwise we would cite sources the model never
    saw in its context.
    """
    parts: list[str] = []
    used: list = []
    total = 0
    for doc in reranked_docs:
        source = _normalize_source(doc.metadata.get("source", "unknown"))
        piece = f"[{source}]\n{doc.page_content}"
        if total + len(piece) > _MAX_CONTEXT_CHARS and parts:
            break
        parts.append(piece)
        used.append(doc)
        total += len(piece)
    return "\n\n".join(parts), used


def retrieve_context(query: str, history: str = "", condensed_query: str = None) -> dict:
    # Reuse a caller-supplied condensed query (e.g. the router already computed
    # it for cache keying) to avoid a second condensation LLM call.
    if condensed_query is None:
        condensed_query = condense_query(query, history)

    logger.info(f"[RAG] Condensed query: {condensed_query}")

    reranked_docs = retrieve_documents(condensed_query)
    context, used_docs = _build_context(reranked_docs)

    return {
        "query": query,
        "condensed_query": condensed_query,
        "documents": reranked_docs,      # full reranked list
        "used_documents": used_docs,     # only those that fit in the context
        "context": context,
    }


def extract_sources(documents: List) -> List[str]:
    sources: list[str] = []
    for doc in documents:
        metadata = getattr(doc, "metadata", {})
        source = _normalize_source(metadata.get("source"))
        if source and source != "unknown" and source not in sources:
            sources.append(source)
    return sources


def answer_rag_question(question: str, memory: ConversationMemory, condensed_query: str = None) -> dict:
    history = memory.get_history()
    trimmed_history = _trim_history(history)

    retrieval = retrieve_context(question, history=trimmed_history, condensed_query=condensed_query)

    if not retrieval["context"].strip():
        answer = NO_ANSWER
    else:
        try:
            answer = qa_chain.invoke(
                {
                    "question": question,
                    "context": retrieval["context"],
                    "history": trimmed_history,
                }
            ).strip()
            # Guard: if the model returns an empty string, use the fallback.
            if not answer:
                answer = NO_ANSWER
        except Exception as e:
            logger.error(f"[RAG] QA chain failed: {e}")
            answer = NO_ANSWER

    memory.add_user(question)
    memory.add_assistant(answer)

    return {
        "answer": answer,
        # Cite ONLY the docs that actually made it into the context the model saw.
        "sources": extract_sources(retrieval["used_documents"]),
        "condensed_query": retrieval["condensed_query"],
    }
