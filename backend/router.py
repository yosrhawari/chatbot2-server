import logging

logger = logging.getLogger(__name__)

import re
import json
from concurrent.futures import ThreadPoolExecutor

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from analytics import analyze_query
from personal_analytics import (
    classify_personal_intent,
    run_personal_analytics,
    _looks_like_bad_output as _bad_personal_output,
    _answer_uses_wrong_currency as _bad_currency,
)
from amount_guard import _answer_invents_money as _invents_money
from rag import (
    retrieve_context,
    answer_rag_question,
    condense_query,
    extract_articles,
    _no_answer,
)
from session import session_manager, ConversationMemory
from language import detect_language, normalize_arabic

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

_LOGIN_REQUIRED_FR = (
    "Pour accéder aux informations personnelles de votre contrat, "
    "veuillez vous connecter."
)
_LOGIN_REQUIRED_AR = (
    "للوصول إلى المعلومات الشخصية لعقدك، يرجى تسجيل الدخول."
)
_DB_UNAVAILABLE_FR = (
    "Le service de données personnelles est temporairement indisponible. "
    "Veuillez réessayer plus tard."
)
_DB_UNAVAILABLE_AR = (
    "خدمة البيانات الشخصية غير متوفرة حالياً. يرجى المحاولة لاحقاً."
)


def _login_required(language: str, memory: ConversationMemory, query: str) -> dict:
    message = _LOGIN_REQUIRED_AR if language == "ar" else _LOGIN_REQUIRED_FR
    memory.add_user(query)
    memory.add_assistant(message)
    return {"type": "login_required", "answer": message, "requires_login": True}


def _db_unavailable(language: str, memory: ConversationMemory, query: str) -> dict:
    message = _DB_UNAVAILABLE_AR if language == "ar" else _DB_UNAVAILABLE_FR
    memory.add_user(query)
    memory.add_assistant(message)
    return {"type": "error", "answer": message}


# Deterministic marker-based routing. Bilingual (FR/AR, via normalize_arabic).
# Principle:
#   personal markers only      -> ANALYTICS
#   documentary markers only   -> RAG
#   personal AND documentary   -> HYBRID
#   no marker                  -> None (LLM fallback keeps current behavior)
# NEVER decides from a bare topic word like "retrait"/"prime"/"epargne"/"contrat".
#
# Matching is word-boundary based for French/Latin tokens: substring matching
# ("ma ") misfired on embedded occurrences ("panorama " contains "ma ").
# Arabic tokens keep plain substring semantics ON PURPOSE — Arabic attaches
# conjunctions/prepositions (و، ب، ل، ف، ك) directly to the word, so \b would
# create false negatives (e.g. "وعقدي" must still match "عقدي").

_FR_PERSONAL_PATTERNS = (
    r"\bcombien ai-je\b", r"\bj'ai verse\b", r"\bj'ai versé\b",
    r"\bcombien ai-je versé\b", r"\bcombien ai-je sur mon\b",
    r"\bquel est mon solde\b",
    r"\bmes beneficiaires\b", r"\bmes bénéficiaires\b",
    r"\bma prime\b", r"\bma primes\b", r"\bmes primes\b",
    r"\bmon beneficiaire\b", r"\bmon bénéficiaire\b",
    r"\bquels sont mes contrats\b",
    r"\bquelles sont mes contrats\b",
    r"\bmes contrats\b",
    r"\bcombien de contrats ai-je\b",
    r"\bcombien ai-je de contrats\b",
    r"\bquelle est mon épargne\b", r"\bquel est mon épargne\b",
    r"\bconsulter mon épargne\b", r"\bretirer mon épargne\b",
    r"\bquelle est la durée de mon contrat\b",
    r"\bquel est mon montant d'épargne\b", r"\bmontant de mon contrat\b",
    r"\bpuis-je\b.*\bmon contrat\b",
    r"\bmontant maximum\b.*\bje peux\b.*\bretirer\b.*\bmon contrat\b",
    r"\bcombien\b.*\bje peux\b.*\bretirer\b.*\bmon contrat\b",
    r"\bmontant maximum\b.*\bje peux\b.*\bretirer\b.*\bactuellement\b",
    r"\bcombien\b.*\bje peux\b.*\bretirer\b.*\bactuellement\b",
)

_AR_PERSONAL_TOKENS = (
    "مدخراتي", "عقدي", "اقساطي", "ادخاري", "أقساطي", "مدخرات",
    "قسط شهر", "قسطي", "دفعت", "دفعات", "المدفوع",
)

_PERSONAL_RE = re.compile(
    "|".join(_FR_PERSONAL_PATTERNS + tuple(map(re.escape, _AR_PERSONAL_TOKENS)))
)

_FR_DOCUMENTARY_PATTERNS = (
    r"\bconditions generales\b", r"\bconditions générale\b", r"\bconditions\b",
    r"\barticle\b", r"\bregles\b", r"\brègles\b", r"\bregle\b", r"\brègle\b",
    r"\bmodalites\b", r"\bmodalités\b", r"\bdefinition\b", r"\bdéfinition\b",
    r"\bque prevoit le contrat\b", r"\bque prévoit le contrat\b",
    r"\bselon le contrat\b", r"\bselon les conditions\b",
    r"\bprocedures\b", r"\bprocédures\b",
    r"\bclauses\b", r"\bchapitre\b", r"\bdispositions\b",
    r"\beffectuer un retrait\b", r"\bfaire un retrait\b",
    r"\bcomment fonctionne\b", r"\bcomment cela fonctionne\b", r"\bfonctionne-t-il\b",
    r"\bavance\b", r"\bavances\b",
    r"\bretrait partiel\b", r"\bretraits partiels\b",
    r"\brachat total\b", r"\brachat partiel\b",
    r"\bversement exceptionnel\b", r"\bversements exceptionnels\b",
    r"\bdélai\b", r"\bdélais\b", r"\bduree\b", r"\bdurée\b",
    r"\bmontant maximum\b", r"\bmontant minimum\b", r"\bmontant maximal\b", r"\bmontant minimal\b",
    r"\binterrompre\b", r"\barrêter\b", r"\barreter\b", r"\barrête\b", r"\barrete\b",
    r"\bque se passe-t-il\b",
    r"\bdécès\b", r"\bdeces\b",
)

_AR_DOCUMENTARY_TOKENS = ("شروط", "قواعد", "احكام", "أحكام", "بنود", "فصل", "نصوص")

_DOCUMENTARY_RE = re.compile(
    "|".join(_FR_DOCUMENTARY_PATTERNS + tuple(map(re.escape, _AR_DOCUMENTARY_TOKENS)))
)


def _normalize_query(query: str) -> str:
    """Lower-case query with Arabic normalization (no secrets involved)."""
    return normalize_arabic(query or "").lower()


def _classify_deterministic(query: str) -> str | None:
    """Marker-based routing. Returns RAG / ANALYTICS / HYBRID or None when no
    marker matches (caller keeps the LLM fallback)."""
    q = _normalize_query(query)
    personal = bool(_PERSONAL_RE.search(q))
    documentary = bool(_DOCUMENTARY_RE.search(q))
    if personal and documentary:
        return "HYBRID"
    if personal:
        return "ANALYTICS"
    if documentary:
        return "RAG"
    return None


def classify_query(query: str) -> str:
    """Return one of RAG / ANALYTICS / HYBRID.

    Deterministic bilingual markers are tried first; the LLM router remains
    the fallback for queries with no marker. Falls back to RAG on error.
    """
    deterministic = _classify_deterministic(query)
    if deterministic is not None:
        logger.debug(f"[ROUTER] deterministic route for {query!r}: {deterministic}")
        return deterministic
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


_HYBRID_SYSTEM_FR = (
    "Tu es un conseiller client de l'assurance HAYETT 2000. Rédige la réponse "
    "en français (la langue de la question).\n"
    "Combine les DEUX sources: les Règles du contrat (Documents) et les Données "
    "du client (Calcul et épargne).\n"
    "FORMAT: sois concis et structuré; traite chaque point sous un court titre en gras suivi "
    "de puces; place les chiffres concrets (montants en DT, pourcentages) dans les puces.\n"
    "RÈGLES:\n"
    "- Tous les montants sont en dinars tunisiens : écris « DT » après un "
    "montant, JAMAIS « € », « EUR » ou « euro ».\n"
    "- Utilise les Données du client et le Calcul fourni pour donner les chiffres précis (ex: épargne totale, montant maximum calculé en DT, montant minimum en DT).\n"
    "- Réponds directement à la question en indiquant le montant maximum exact en DT que le client peut demander (par exemple 75 % de l'épargne pour un retrait partiel, ou 80 % pour une avance) ainsi que les conditions requises.\n"
    "- Pour citer un article, mentionne le numéro et le titre de l'article (ex: [ARTICLE: ARTICLE 10 | TITRE: RETRAIT ANTICIPE...]).\n"
    "- Mentionne que ce calcul est indicatif et que le traitement réel est effectué par l'assureur.\n"
    "- N'affiche JAMAIS de JSON brut, de blocs de code ni de liste d'objets."
)

_HYBRID_SYSTEM_AR = (
    "أنت مستشار عملاء شركة التأمين \"HAYETT 2000\". اكتب الإجابة بالعربية "
    "(لغة السؤال).\n"
    "اجمع بين المصدرين: قواعد العقد (المستندات) وبيانات وحسابات العميل.\n"
    "التنسيق: كن موجزاً ومنظماً؛ عالج كل نقطة بعنوان قصير بخط عريض ثم بنقاط؛ ضع "
    "الأرقام الملموسة (المبالغ بالدينار، النسب) في النقاط.\n"
    "القواعد:\n"
    "- جميع المبالغ بالدينار التونسي: اكتب «دينار» بعد أي مبلغ، وأبداً «€» أو «يورو» أو «EUR».\n"
    "- استخدم بيانات العميل والحساب المقدَّم لتحديد الأرقام بدقة (إجمالي الادخار، أقصى مبلغ محسوب بالدينار، الحد الأدنى بالدينار).\n"
    "- أجب مباشرة على السؤال مع توضيح أقصى مبلغ متاح بالدينار (مثلاً 75% للانسحاب الجزئي أو 80% للتسبيق) مع الشروط.\n"
    "- عند الاستشهاد بفصل، اذكر رقم واسم الفصل.\n"
    "- وضح أن هذا الحساب تقديري والمعالجة الفعلية تقوم بها شركة التأمين.\n"
    "- لا تعرض أبداً JSON خام أو كتل تعليمات برمجية أو قوائم كائنات."
)

_HYBRID_USER = (
    "Règles du contrat (contexte documentaire):\n{rag_context}\n\n"
    "Données du client (ne pas coller ce bloc brut dans la réponse):\n"
    "{analytics_context}\n\n"
    "Historique:\n{history}\n\n"
    "Question:\n{question}\n\n"
    "Rédige la réponse dans la langue de la question, sans JSON."
)


def _make_hybrid_chain(language: str):
    system = _HYBRID_SYSTEM_AR if language == "ar" else _HYBRID_SYSTEM_FR
    return (
        ChatPromptTemplate.from_messages([
            ("system", system),
            ("user", _HYBRID_USER),
        ])
        | llm_client
        | StrOutputParser()
    )


def handle_analytics(query: str, memory: ConversationMemory) -> dict:
    result = analyze_query(query)
    answer = result.get("answer", "Aucune réponse trouvée.")

    memory.add_user(query)
    memory.add_assistant(answer)

    return {"type": "analytics", "answer": answer, "data": result.get("data")}


def handle_personal_analytics(query: str, client_id: int, memory: ConversationMemory) -> dict:
    """Controlled Oracle analytics, always scoped to the authenticated client."""
    result = run_personal_analytics(query, client_id)

    memory.add_user(query)
    memory.add_assistant(result["answer"])

    return {
        "type": "analytics",
        "answer": result["answer"],
        "data": result.get("data"),
        "intent": result.get("intent"),
        "requires_login": False,
    }


def handle_rag(query: str, memory: ConversationMemory, condensed_query: str = None) -> dict:
    result = answer_rag_question(query, memory, condensed_query=condensed_query)
    return {
        "type": "rag",
        "answer": result["answer"],
        "sources": result["sources"],
        # Article titles come from the chunk metadata (verbatim), never from
        # the LLM. Surfaced to the UI and used by the hybrid fallback.
        "articles": result.get("articles", []),
        "language": result.get("language", ""),
        # Surface the condensed query so the router can key the cache by it.
        "condensed_query": result["condensed_query"],
    }


def handle_hybrid(query: str, client_id: int, memory: ConversationMemory) -> dict:
    """RAG rules + Oracle personal data, computed in parallel, synthesised by
    the LLM in the language of the question."""
    history = memory.get_history()
    language = detect_language(query)

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_rag = executor.submit(retrieve_context, query, history)
        future_personal = executor.submit(run_personal_analytics, query, client_id)
        retrieval_result = future_rag.result()
        analytics_result = future_personal.result()

    analytics_data = analytics_result.get("data", [])
    analytics_context = json.dumps(
        {
            "intent": analytics_result.get("intent"),
            "rows": analytics_data,
            "calcul": analytics_result.get("calculation", {}),
        },
        ensure_ascii=False,
        default=str,
    )
    if len(analytics_context) > 2500:
        analytics_context = analytics_context[:2500] + " ...(truncated)"

    rag_context = retrieval_result.get("context", "")

    answer = None
    # No personal rows -> never let the LLM compose financial figures from
    # nothing (hallucination risk). Use the deterministic personal summary.
    if analytics_data:
        try:
            answer = _make_hybrid_chain(language).invoke({
                "question": query,
                "analytics_context": analytics_context,
                "rag_context": rag_context,
                "history": history,
            }).strip()
            if (_bad_personal_output(answer)
                    or _bad_currency(answer)
                    or _invents_money(
                        answer, analytics_data,
                        analytics_result.get("calculation", {}))
                    or _looks_like_bad_user_facing_answer(answer)):
                answer = None
        except Exception as e:
            logger.warning(f"[HYBRID] Generation failed, fallback used: {e}")
            answer = None

    # Deterministic fallback: personal summary + cited article titles.
    if not answer:
        answer = analytics_result.get("answer", "")
        articles = extract_articles(retrieval_result.get("used_documents", []))
        if articles:
            cited = "\n".join(
                f"- {a['article']} — {a['title']} ({a['source']})" for a in articles
            )
            label = "Articles de référence" if language != "ar" else "الفصول المرجعية"
            answer = (answer.rstrip() + f"\n\n**{label}**\n" + cited).strip()

    memory.add_user(query)
    memory.add_assistant(answer)

    return {
        "type": "hybrid",
        "answer": answer,
        "sources": [
            doc.metadata.get("source")
            for doc in retrieval_result.get("used_documents", retrieval_result["documents"])
            if doc.metadata.get("source")
        ],
        "articles": extract_articles(retrieval_result.get("used_documents", [])),
        "data": analytics_data,
        "intent": analytics_result.get("intent"),
        "requires_login": False,
    }


def route_query(query: str, session_id: str, client_id: int = None) -> dict:
    """Route a query for a specific session.

    `session_id` is REQUIRED. Callers must pass a stable per-user/per-chat id.
    `client_id` (from the server-side auth store) scopes every personal-data
    request; it is never taken from the question text.

    Security rules (plan §22):
    - RAG stays available to everyone.
    - ANALYTICS / HYBRID refuse politely when no client is authenticated.
    """
    if not session_id:
        raise ValueError("session_id is required")

    memory = session_manager.get_memory(session_id)
    query_type = classify_query(query)
    logger.debug(
        "[ROUTER] client_id=%s query_type=%s query=%r",
        client_id,
        query_type,
        (query or "")[:120],
    )

    if query_type == "ANALYTICS":
        if client_id is None:
            return _login_required(detect_language(query), memory, query)
        # Personal contract question (Oracle) or company dataset (pandas/CSV)?
        intent = classify_personal_intent(query)
        logger.debug("[ROUTER] client_id=%s intent=%s", client_id, intent)
        try:
            if intent != "unknown":
                return handle_personal_analytics(query, client_id, memory)
            return handle_analytics(query, memory)  # pandas/CSV path kept in parallel
        except Exception as e:
            logger.error(f"[ROUTER] Personal analytics failed: {e}")
            return _db_unavailable(detect_language(query), memory, query)

    if query_type == "HYBRID":
        if client_id is None:
            return _login_required(detect_language(query), memory, query)
        try:
            return handle_hybrid(query, client_id, memory)
        except Exception as e:
            logger.error(f"[ROUTER] Hybrid failed: {e}")
            return _db_unavailable(detect_language(query), memory, query)

    # RAG route: safe to cache (shared documentation knowledge).
    # Key the cache by the CONDENSED (standalone, history-independent) query,
    # NOT the raw follow-up. A follow-up like "et son prix?" only makes sense
    # against a specific history, so caching its answer under the raw text and
    # serving it to another session would be wrong. The condensed query is
    # self-contained and safe to share. We condense ONCE here and reuse it in
    # handle_rag so there is no second condensation LLM call.
    history = memory.get_history(max_turns=1, max_chars=800)
    condensed = condense_query(query, history)

    cached = semantic_cache.get_cached_response(condensed)
    if cached is not None:
        memory.add_user(query)
        memory.add_assistant(cached.get("answer", ""))
        return cached

    response = handle_rag(query, memory, condensed_query=condensed)
    ans = response.get("answer", "")
    is_refusal = isinstance(ans, str) and ("documentation ne contient pas" in ans or "لا تحتوي الوثائق" in ans)
    if ans and not is_refusal:
        semantic_cache.add_to_cache(response.get("condensed_query", condensed), response)
    return response
