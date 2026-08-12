import logging

logger = logging.getLogger(__name__)

import re
from typing import List
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document
from langchain_classic.retrievers import ContextualCompressionRetriever

from config import (
    VECTOR_SEARCH_TOP_K,
    RERANKER_TOP_K,
    MAX_CONTEXT_CHARS,
)

from ingest import get_vectorstore, normalize_arabic_query, chapter_hint, chapter_reference
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
# Condensed to the rules that matter most for a small model:
# grounding, no hallucination, direct structured style, source citation,
# French output.
qa_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "Tu es un assistant commercial. Règles strictes:\n"
     "1. Réponds TOUJOURS en français, même si la question ou le contexte est "
     "en arabe: traduis alors l'essentiel en français.\n"
     "2. Réponds UNIQUEMENT avec les informations du contexte fourni.\n"
     "3. Commence DIRECTEMENT par un titre en gras (ex: **...**) puis des "
     "puces concises. N'écris AUCUNE phrase d'introduction (pas de «compte "
     "tenu», «d'après le document») ni de conclusion (pas de «il convient de "
     "noter», «il est important de souligner»).\n"
     "4. Cite la source entre crochets après chaque fait, ex: [document.pdf].\n"
     "5. Si la réponse est absente du contexte, réponds exactement: "
     "\"Je suis désolé, mais la documentation ne contient pas les informations "
     "nécessaires pour répondre à cette question.\"\n"
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


def _build_search_terms(condensed_query: str) -> List[str]:
    """Search terms for retrieval, best first.

    The condensed query is Arabic-normalised so it matches the repaired
    Arabic index. A canonical chapter reference («الفصل9 1» → «الفصل 9»)
    becomes the primary term so glued digits and stray numbers do not pollute
    the embedding. When the query references a numbered chapter/article, a
    term with a French hint is appended so the French copy of the document
    also surfaces (the reranker picks the relevant chunks either way).
    """
    normalized = normalize_arabic_query(condensed_query)
    canonical = chapter_reference(normalized)
    terms = [canonical or normalized]
    if canonical and canonical != normalized:
        terms.append(normalized)
    hint = chapter_hint(normalized)
    if hint:
        terms.append(f"{terms[0]} {hint}")
    return terms


def _dedupe_documents(primary, secondary):
    seen = set()
    merged = []
    for doc in list(primary) + list(secondary):
        key = (doc.metadata.get("source", ""), doc.page_content)
        if key in seen:
            continue
        seen.add(key)
        merged.append(doc)
    return merged


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


def _priority_sort_for_chapter(docs, marker_parts):
    """Put chunks containing a requested chapter/article marker first.

    Numbered references («الفصل 9») match both the Arabic marker line
    («الفصل 9 …») and its French equivalent («ARTICLE 9. …») in the parallel
    copy. Boosting them above adjacent chapters keeps the small model focused
    on the requested content. Stable: the reranker order is preserved within
    each group.
    """
    if not docs or not marker_parts:
        return docs
    hits = [i for i, doc in enumerate(docs) if any(m in doc.page_content for m in marker_parts)]
    if not hits:
        return docs
    hit_set = set(hits)
    return [docs[i] for i in hits] + [docs[i] for i in range(len(docs)) if i not in hit_set]


def _chapter_markers(condensed_query: str) -> List[str]:
    """Marker substrings that identify the requested chapter/article in both
    language copies, e.g. «الفصل 9» → ['الفصل 9', 'ARTICLE 9.', 'article 9']."""
    canon = chapter_reference(normalize_arabic_query(condensed_query))
    if not canon:
        return []
    m = re.match(r"^([^\d\s]+)\s+(\d+)$", canon)
    if not m:
        return []
    label, num = m.groups()
    markers = [canon]
    if label in ("الفصل", "فصل"):
        markers.append(f"ARTICLE {num}.")
        markers.append(f"ARTICLE {num}\n")
        markers.append(f"article {num}")
    else:
        markers.append(f"chapitre {num}")
    return markers


def _chapter_lookup_chunks(markers: List[str]) -> List:
    """Fetch stored chunks matching a numbered chapter/article marker.

    Semantic retrieval can miss the exact section when embeddings blur
    adjacent chapters, so for numbered references we also scan the (small)
    collection directly. A marker chunk is included together with the chunk
    that follows it in the source, which usually carries the section body
    (e.g. the «الفصل 9 تسبيقات على العقد» header precedes the advances
    details).
    """
    if not markers:
        return []
    data = vectorstore.get(include=["documents", "metadatas"])
    contents = data.get("documents") or []
    metas = data.get("metadatas") or []
    matching = [i for i, c in enumerate(contents) if any(m in c for m in markers)]
    indices = set(matching)
    # The successor chunk (next in the same source) also belongs to the section.
    for i in matching:
        src = (metas[i] or {}).get("source")
        for j in range(i + 1, len(contents)):
            if (metas[j] or {}).get("source") != src:
                break
            indices.add(j)
            break
    # Section-body chunks (marker near the start, e.g. «ARTICLE 9. AVANCES…»)
    # come first: the small model then grounds its answer on the section
    # content instead of the previous chapter's spillover.
    ordered = sorted(
        indices,
        key=lambda i: (
            # Arabic marker line «الفصل N …» is also a body start; a French
            # body chunk (marker right after the prefix) leads either way.
            0 if any(m in contents[i][:80] for m in markers) else 1,
            i,
        ),
    )
    return [
        Document(page_content=contents[i], metadata=metas[i] or {})
        for i in ordered
    ]


def retrieve_context(query: str, history: str = "", condensed_query: str = None) -> dict:
    # Reuse a caller-supplied condensed query (e.g. the router already computed
    # it for cache keying) to avoid a second condensation LLM call.
    if condensed_query is None:
        condensed_query = condense_query(query, history)

    logger.info(f"[RAG] Condensed query: {condensed_query}")

    terms = _build_search_terms(condensed_query)
    if len(terms) == 1:
        reranked_docs = retrieve_documents(terms[0])
    else:
        # Two search terms: retrieve both raw lists, merge, then rerank once
        # so the bilingual candidates compete fairly for the top slots.
        merged = _dedupe_documents(
            base_retriever.invoke(terms[0]),
            base_retriever.invoke(terms[1]),
        )
        reranked_docs = reranker_compressor.compress_documents(merged, terms[0])

    # Numbered chapter references get their marker chunks promoted to the
    # front of the context so adjacent chapters do not dominate the answer.
    # Direct lookup guarantees the section body is present even when the
    # embeddings blur it; the reranked candidates fill the remaining budget.
    markers = _chapter_markers(condensed_query)
    if markers:
        lookup = _chapter_lookup_chunks(markers)
        reranked_docs = _priority_sort_for_chapter(reranked_docs, markers)
        reranked_docs = lookup + [
            d for d in reranked_docs
            if all(l.page_content != d.page_content for l in lookup)
        ]
        logger.info(f"[RAG] Chapter priority markers: {markers} ({len(lookup)} direct hits)")

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
