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
from language import detect_language
from session import ConversationMemory

from models import (
    llm_client,
    reranker_compressor,
)

NO_ANSWER = (
    "Je suis désolé, mais la documentation ne contient pas les informations "
    "nécessaires pour répondre à cette question."
)
NO_ANSWER_AR = "عذراً، لا تحتوي الوثائق على المعلومات اللازمة للإجابة على هذا السؤال."


def _no_answer(lang: str) -> str:
    return NO_ANSWER_AR if lang == "ar" else NO_ANSWER

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
_MAX_CONTEXT_CHARS = min(MAX_CONTEXT_CHARS, 3000)  # hard cap for this model

# ── History-Aware Query Condensation ─────────────────────────────────────────
condense_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "You are an expert search query optimizer for an insurance assistant.\n"
     "Given the conversation history and a follow-up question, rewrite it into a standalone search query ONLY if it contains ambiguous pronouns (like 'il', 'elle', 'son', 'sa', 'celui-ci', 'cette option', 'هذا', 'هو').\n"
     "Rules:\n"
     "1. If the question is ALREADY a standalone, specific question on a clear topic (e.g. 'Puis-je effectuer un versement exceptionnel ?', 'Quelles sont les conditions de rachat ?'), return the question EXACTLY as written without changing a single word.\n"
     "2. NEVER invent, merge, or add topics from previous history into a question asking about a different concept.\n"
     "3. Return ONLY the search query text, with no explanations, no quotes, no conversational filler."),
    ("user", "History:\n{history}\n\nQuestion: {query}\n\nStandalone Search Query:"),
])
query_condenser = condense_prompt | llm_client | StrOutputParser()

# ── QA Answering Chain ────────────────────────────────────────────────────────
# The language of the question determines the language of the answer. Each
# question runs through the prompt template of its own language; articles
# cited in the answer must be copied VERBATIM from the [ARTICLE: ... |
# TITRE: ...] labels embedded in the context (never invented by the model).

_QA_SYSTEM_FR = (
    "Tu es l'assistant officiel du contrat d'assurance HAYETT 2000.\n"
    "Consignes pour répondre :\n"
    "1. Langue : Réponds TOUJOURS en français de manière claire, concise et professionnelle.\n"
    "2. Source : Réponds à la question posée en te basant sur les informations du contexte fourni.\n"
    "3. Citation : Mentionne le numéro et le titre de l'article concerné (par exemple : **Article 9 - Avances sur contrat**).\n"
    "4. Précision : Explique la règle applicable et donne les détails importants (conditions, pourcentages, délais, montants).\n"
    "5. Montants : Tous les montants sont en dinars tunisiens (écris « DT »).\n"
    "6. Absence d'information : Si et seulement si le contexte ne contient aucun élément pour répondre à la question, réponds exactement : "
    "\"Je suis désolé, mais la documentation ne contient pas les informations nécessaires pour répondre à cette question.\""
)

_QA_SYSTEM_AR = (
    "أنت المساعد الرسمي لعقد التأمين \"HAYETT 2000\".\n"
    "تعليمات الإجابة:\n"
    "1. اللغة: أجب دائماً بالعربية بطريقة واضحة ودقيقة ومهنية.\n"
    "2. المصدر: أجب عن السؤال اعتماداً على المعلومات الواردة في السياق المقدم.\n"
    "3. الاستشهاد: اذكر رقم واسم الفصل المعني (مثال: **الفصل 9 - تسبيقات على العقد**).\n"
    "4. الدقة: اشرح القاعدة المعمول بها واذكر الأرقام، النسب المئوية، الشروط أو الآجال المذكورة في النص.\n"
    "5. المبالغ: جميع المبالغ بالدينار التونسي (اكتب «دينار»).\n"
    "6. غياب المعلومة: فقط إذا كان السياق لا يحتوي على أي معلومة للإجابة، أجب حرفياً: "
    "\"عذراً، لا تحتوي الوثائق على المعلومات اللازمة للإجابة على هذا السؤال.\""
)

_QA_USER = (
    "Contexte documentaire:\n{context}\n\n"
    "Question: {question}\n\n"
    "Réponse:"
)

_QA_USER_WITH_HISTORY = (
    "Historique récent:\n{history}\n\n"
    "Contexte documentaire:\n{context}\n\n"
    "Question: {question}\n\n"
    "Réponse:"
)


def _make_qa_prompt(lang: str, has_history: bool = False):
    system = _QA_SYSTEM_AR if lang == "ar" else _QA_SYSTEM_FR
    user_tpl = _QA_USER_WITH_HISTORY if has_history else _QA_USER
    return ChatPromptTemplate.from_messages([
        ("system", system),
        ("user", user_tpl),
    ])


# ── Helpers ───────────────────────────────────────────────────────────────────


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


_FOLLOW_UP_ANAPHORA = re.compile(
    r"\b(et\s+pour|et\s+si|dans\s+ce\s+cas|dans\s+cette\s+situation|"
    r"celui-ci|celle-ci|ceux-ci|celles-ci|son\b|sa\b|ses\b|leur\b|leurs\b|"
    r"lui|en\b|y\b)\b|"
    r"(وكيف|وماذا|وهل|ولماذا|وكم|في\s+هذه\s+الحالة|هذا|هذه|ذلك|تلك|له|لها|عنه|عنها)",
    re.IGNORECASE,
)

_STANDALONE_TOPICS = re.compile(
    r"\b(versement\s+exceptionnel|versements\s+exceptionnels|rachat\s+total|rachat\s+partiel|avance|avances|"
    r"clause\s+bénéficiaire|bénéficiaire|bénéficiaires|garantie\s+décès|décès|"
    r"résiliation|renonciation|délai\s+de\s+renonciation|délai\s+de\s+résiliation|"
    r"arbitrage|frais\s+de\s+gestion|frais\s+sur\s+versement|conditions\s+générales|"
    r"article\s+\d+|chapitre\s+\d+|dispositions)\b|"
    r"(تسبيق|تسبقة|استرداد|تصفية|المستفيد|وفاة|إلغاء|فسخ|فصل|شروط\s+عامة)",
    re.IGNORECASE,
)


def is_standalone_query(query: str) -> bool:
    """Detect queries that are already complete standalone questions and must NOT be mutated by condensation."""
    q = (query or "").strip()
    if not q:
        return True
    # If the query explicitly mentions a clear insurance topic and does not contain relative anaphora, keep raw
    if _STANDALONE_TOPICS.search(q) and not _FOLLOW_UP_ANAPHORA.search(q):
        return True
    return False


def condense_query(query: str, history: str = "") -> str:
    """Rewrite a follow-up into a standalone, history-independent query.

    Exposed at module level so the router can key the semantic cache by the
    CONDENSED query (which is safe to share across sessions) instead of the raw
    follow-up text (whose meaning depends on the caller's history). Returns the
    raw query unchanged when there is no history or on any failure.
    """
    q_clean = (query or "").strip()
    if not history.strip() or not q_clean:
        return q_clean
    if is_standalone_query(q_clean):
        return q_clean
    try:
        condensed = query_condenser.invoke(
            {"query": q_clean, "history": history.strip()}
        ).strip()
        # Fallback: if the model echoes back something too long or empty, use raw query.
        if not condensed or len(condensed) > 400:
            return q_clean
        # Strip potential wrapping quotes
        if (condensed.startswith('"') and condensed.endswith('"')) or (condensed.startswith("'") and condensed.endswith("'")):
            condensed = condensed[1:-1].strip()
        return condensed or q_clean
    except Exception as e:
        logger.warning(f"[RAG] Query condensation failed, using raw query: {e}")
        return q_clean


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


def _article_label(doc) -> str:
    """Prefix for a context piece. When the chunk carries article metadata,
    emit the verbatim [ARTICLE: … | TITRE: …] label the model must reuse;
    otherwise fall back to the bare source filename."""
    metadata = getattr(doc, "metadata", {})
    article = metadata.get("article")
    title = metadata.get("title")
    if article and title:
        return f"[ARTICLE: {article} | TITRE: {title}]"
    if article:
        return f"[ARTICLE: {article}]"
    return f"[{_normalize_source(metadata.get('source', 'unknown'))}]"


def _build_context(reranked_docs):
    """Concatenate chunks up to a character budget.

    Returns (context_string, used_docs) where used_docs are ONLY the documents
    that actually fit inside the budget. Callers must cite from used_docs, not
    the full reranked list -- otherwise we would cite sources the model never
    saw in its context. Every piece is prefixed by its article label so the
    model can quote the exact article title from the document metadata.
    """
    parts: list[str] = []
    used: list = []
    total = 0
    for doc in reranked_docs:
        label = _article_label(doc)
        piece = f"{label}\n{doc.page_content}"
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


def extract_articles(documents: List) -> List[dict]:
    """Article provenance of the chunks actually used in the answer.

    Returns the article number, VERBATIM title, source and language straight
    from the chunk metadata -- never invented by the LLM.
    """
    articles: list[dict] = []
    seen = set()
    for doc in documents:
        metadata = getattr(doc, "metadata", {})
        article = metadata.get("article")
        if not article:
            continue
        key = (str(article), str(metadata.get("title", "")), str(metadata.get("source", "")))
        if key in seen:
            continue
        seen.add(key)
        articles.append(
            {
                "article": str(article),
                "title": str(metadata.get("title", "")),
                "source": _normalize_source(metadata.get("source")),
                "language": str(metadata.get("language", "")),
            }
        )
    return articles


def _is_title_only(ans: str) -> bool:
    """Detect if the answer is only an article title without substantive content."""
    t = ans.strip()
    # Regex matching only a bold article title with no other content (French/English)
    if re.fullmatch(r"\s*\*\*ARTICLE[^*]*\*\*\s*", t):
        return True
    # Arabic article title only
    if re.fullmatch(r"\s*\*\*(?:الفصل|المادة)\s+\d+[^*]*\*\*\s*", t):
        return True
    # Short response with ARTICLE/الفصل/المادة but no real explanation
    t_lower = t.lower()
    if ("article" in t_lower or "الفصل" in t or "المادة" in t) and len(t.split()) < 20 and "peut" not in t_lower and "يمكن" not in t_lower and t.count(".") < 1 and "-" not in t:
        return True
    return False


def answer_rag_question(question: str, memory: ConversationMemory, condensed_query: str = None) -> dict:
    language = detect_language(question)
    # Use windowed recent history (last 1 turn) to prevent context pollution in small LLMs
    recent_history = memory.get_history(max_turns=1, max_chars=800)
    has_history = bool(recent_history.strip())

    retrieval = retrieve_context(question, history=recent_history, condensed_query=condensed_query)

    if not retrieval["context"].strip():
        answer = _no_answer(language)
    else:
        qa_chain = _make_qa_prompt(language, has_history=has_history) | llm_client | StrOutputParser()
        try:
            inputs = {
                "question": question,
                "context": retrieval["context"],
            }
            if has_history:
                inputs["history"] = recent_history

            answer = qa_chain.invoke(inputs).strip()
            # Guard: if the model returns an empty string, use the fallback.
            if not answer:
                answer = _no_answer(language)
            # Guard: if the answer is only an article title without explanation, retry once
            elif _is_title_only(answer):
                logger.warning(f"[RAG] Title-only answer detected, retrying for: {question!r}")
                retry_system = (
                    _QA_SYSTEM_FR
                    + "\n7. Ta réponse précédente était uniquement le titre. Réécris une réponse COMPLÈTE en expliquant la règle, toujours avec le titre exact."
                ) if language != "ar" else (
                    _QA_SYSTEM_AR
                    + "\n7. Ta réponse précédente était uniquement le titre. Réécris une réponse COMPLÈTE en expliquant la règle, toujours avec le titre exact."
                )
                retry_user = _QA_USER_WITH_HISTORY if has_history else _QA_USER
                retry_prompt = ChatPromptTemplate.from_messages([
                    ("system", retry_system),
                    ("user", retry_user),
                ])
                try:
                    retry = (retry_prompt | llm_client | StrOutputParser()).invoke(inputs).strip()
                    if retry and not _is_title_only(retry):
                        answer = retry
                    else:
                        logger.warning(f"[RAG] Retry still title-only, keeping original for: {question!r}")
                except Exception as e:
                    logger.warning(f"[RAG] Retry failed: {e}")
        except Exception as e:
            logger.error(f"[RAG] QA chain failed: {e}")
            answer = _no_answer(language)

    memory.add_user(question)
    memory.add_assistant(answer)

    return {
        "answer": answer,
        # Cite ONLY the docs that actually made it into the context the model saw.
        "sources": extract_sources(retrieval["used_documents"]),
        # Article titles come from the chunk metadata, not from the LLM.
        "articles": extract_articles(retrieval["used_documents"]),
        "language": language,
        "condensed_query": retrieval["condensed_query"],
    }
