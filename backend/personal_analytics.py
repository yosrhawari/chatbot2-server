import logging

logger = logging.getLogger(__name__)

import re
from datetime import date
from decimal import Decimal

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from language import detect_language, normalize_arabic
from models import llm_client
from amount_guard import (
    _AMOUNT_RE,
    _YEAR_RE,
    _as_float,
    _parse_amount_token,
    _answer_invents_money,
)

try:
    from database import (
        list_contrats,
        latest_epargne,
        versements_annee,
        list_beneficiaires,
        DatabaseUnavailable,
    )
except Exception:  # pragma: no cover - tests import with mocked models
    list_contrats = latest_epargne = versements_annee = list_beneficiaires = None
    DatabaseUnavailable = RuntimeError


# ── Controlled intents ───────────────────────────────────────────────────────
# The router maps the question to ONE of these intents; each intent runs a
# fixed, parameterised SQL template that ALWAYS filters by the authenticated
# client_id. The LLM never writes SQL and never sees other clients' data.

_ORDERED_INTENTS = (
    "versements_annee",
    "retrait",
    "epargne",
    "prime",
    "beneficiaire",
    "contrat",
)

# (normalised keyword, weight); Arabic keywords are normalised via
# normalize_arabic (diacritics/alef variants/indic digits).
_INTENT_KEYWORDS = {
    "versements_annee": [
        ("verse", 1), ("versement", 1), ("verse ", 1), ("versé", 1),
        ("paye ", 1), ("payé", 1), ("paiement de mes", 1), ("paiements", 1),
        ("قسط", 1), ("سدد", 1), ("تسديد", 1),
        ("cette année", 2), ("this year", 2), ("ces 6 mois", 2), ("année", 1), ("سنة", 1),
    ],
    "retrait": [
        ("retrait", 2), ("rachat", 2), ("retirer", 2), ("retire", 2),
        ("remboursement", 1), ("الانسحاب", 2), ("الاسترداد", 2),
        ("انسحاب", 2), ("استرداد", 2), ("اشتراء", 1),
    ],
    "epargne": [
        ("epargne", 2), ("épargne", 2), ("epargne constituee", 3), ("épargne constituée", 3),
        ("mon capital", 1), ("mon epargne", 3), ("mon épargne", 3),
        ("ادخار", 2), ("مدخر", 2), ("رأس المال", 1), ("راس المال", 1),
    ],
    "prime": [
        ("prime", 2), ("primes", 2), ("mensuel", 1), ("mensuelle", 1),
        ("mensualité", 1), ("échéance", 1),
        ("قسط شهري", 2), ("قسط", 1), ("قسطي", 3), ("القسط الشهري", 3),
        ("الدورية", 1),
    ],
    "beneficiaire": [
        ("beneficiaire", 2), ("bénéficiaire", 2), ("المستفيد", 2), ("مستفيد", 2),
    ],
    "contrat": [
        ("contrat", 1), ("durée", 1), ("duree", 1), ("statut", 1),
        ("d'un contrat", 1), ("عقد", 1), ("مدة", 1),
    ],
}

# Guard: general rule questions without possessive must not be treated as personal.
# Mirrors the strong personal markers in router.py (no bare mon/ma/mes).
_PERSONAL_GUARD_FR = (
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
)
_PERSONAL_GUARD_AR = (
    "مدخراتي", "عقدي", "اقساطي", "ادخاري", "أقساطي", "مدخرات",
    "قسط شهر", "قسطي", "دفعت", "دفعات", "المدفوع",
)
_PERSONAL_GUARD_RE = re.compile(
    "|".join(_PERSONAL_GUARD_FR + tuple(map(re.escape, _PERSONAL_GUARD_AR)))
)


def _has_any(normalized_query: str, words) -> bool:
    return any(w in normalized_query for w in words)


def classify_personal_intent(query: str) -> str:
    """Map a question onto a controlled analytics intent.

    Keyword scoring first (deterministic, bilingual), LLM fallback when
    nothing matches. Returns "unknown" when the question is not a personal
    contract-data question (the router then keeps the question on the
    pandas/CSV analytics path if appropriate).
    """
    q_ar = normalize_arabic(query).lower()
    q_fr = (query or "").lower()

    # General Conditions Générales without possessive -> not a personal Oracle query
    if not _PERSONAL_GUARD_RE.search(q_ar) and not _PERSONAL_GUARD_RE.search(q_fr):
        return "unknown"

    best_intent, best_score = None, 0
    for intent in _ORDERED_INTENTS:
        score = 0
        for word, weight in _INTENT_KEYWORDS[intent]:
            if word in q_ar or word in q_fr:
                score += weight
        if score > best_score:
            best_intent, best_score = intent, score

    if best_intent is not None:
        return best_intent

    # LLM fallback: single word from the intent list.
    try:
        raw = (
            ChatPromptTemplate.from_messages([
                ("system",
                 "Classify the client question into EXACTLY ONE of: "
                 "versements_annee, retrait, epargne, prime, beneficiaire, "
                 "contrat, unknown. Reply with a single lowercase word only."),
                ("user", "{query}"),
            ])
            | llm_client
            | StrOutputParser()
        ).invoke({"query": query}).strip().lower()
        for intent in _ORDERED_INTENTS:
            if intent in raw:
                return intent
    except Exception as e:
        logger.warning(f"[PERSONAL] Intent fallback failed: {e}")
    return "unknown"


# ── Deterministic calculations ───────────────────────────────────────────────

def compute_retrait_bounds(epargne: float) -> dict:
    """Plan §18: retrait partiel autorisé entre 10 % et 75 % de l'épargne."""
    return {
        "min_10_pourcent": round(epargne * 0.10, 2),
        "max_75_pourcent": round(epargne * 0.75, 2),
    }


def _requested_year(query: str) -> int | None:
    """Calendar year explicitly written in the question (e.g. 2025), or None."""
    match = _YEAR_RE.search(query or "")
    if match:
        return int(match.group(1))
    return None


def find_requested_amount(query: str) -> float | None:
    """First likely money amount in the question."""
    q = query or ""
    for match in _AMOUNT_RE.finditer(q):
        value = _parse_amount_token(match.group(0))
        if value is not None and value > 0:
            return value
    return None


# ── Anti-hallucination guard ─────────────────────────────────────────────────
# The money-whitelist guard (_answer_invents_money) lives in amount_guard.py
# so the analytics and hybrid composers are protected by the exact same check.

# ── Data building (controlled SQL per intent) ────────────────────────────────

def _clean_rows(rows: list) -> list:
    """Make Oracle rows JSON-friendly (dates/decimals)."""
    cleaned = []
    for row in rows or []:
        clean = {}
        for k, v in row.items():
            if isinstance(v, (date,)):
                clean[k] = v.isoformat()
            elif isinstance(v, Decimal):
                clean[k] = float(v)
            else:
                clean[k] = v
        cleaned.append(clean)
    return cleaned


def _intent_data(intent: str, client_id, query: str) -> tuple:
    """Run the controlled query for an intent. Returns (data, calculation).

    Every query path goes through database helpers that filter by
    :client_id — the client can only ever see their own rows.
    """
    if intent == "epargne":
        return _clean_rows(latest_epargne(client_id)), {}
    if intent == "versements_annee":
        year = _requested_year(query) or date.today().year
        return _clean_rows(versements_annee(client_id, year)), {"annee": year}
    if intent == "retrait":
        epargne_rows = _clean_rows(latest_epargne(client_id))
        calculation = {}
        if epargne_rows:
            total = sum(_as_float(r.get("montant_epargne")) or 0 for r in epargne_rows)
            calculation = compute_retrait_bounds(total)
            calculation["epargne_totale"] = round(total, 2)
            amount = find_requested_amount(query)
            if amount is not None:
                calculation["montant_demande"] = amount
                calculation["autorisable"] = (
                    calculation["min_10_pourcent"] <= amount <= calculation["max_75_pourcent"]
                )
        return epargne_rows, calculation
    if intent == "prime":
        rows = _clean_rows(list_contrats(client_id))
        for row in rows:
            row["periodicite"] = (row.get("periodicite") or "").title() or "Mensuel"
        return rows, {}
    if intent == "beneficiaire":
        return _clean_rows(list_beneficiaires(client_id)), {}
    # "contrat" (default) and any other personal question -> contract overview.
    return _clean_rows(list_contrats(client_id)), {}


# ── Answer composition (LLM, bilingual) ──────────────────────────────────────

def _fmt_amount(value) -> str:
    """Deterministic, locale-free amount formatting: 1500 -> '1500',
    18500.56 -> '18500.56', 0 -> '0'."""
    try:
        s = f"{float(value):.2f}".rstrip("0").rstrip(".")
        return "0" if s == "-0" else s
    except (TypeError, ValueError):
        return str(value)


def _fmt_grouped(value) -> str:
    """Deterministic, locale-free amount formatting with space thousand
    grouping: 1500 -> '1 500', 18500.56 -> '18 500.56', 0 -> '0'."""
    v = _as_float(value)
    if v is None:
        return str(value)
    if v == int(v):
        return f"{int(v):,}".replace(",", " ")
    return f"{v:,.2f}".rstrip("0").rstrip(".").replace(",", " ")


_COMPOSER_SYSTEM_FR = (
    "Tu es un conseiller client de l'assurance HAYETT 2000. "
    "Réponds en français, avec un titre court en gras puis des puces concises.\n"
    "Utilise UNIQUEMENT les données fournies — n'invente aucun chiffre.\n"
    "Toutes les sommes sont en dinars tunisiens : écris toujours « DT » après "
    "un montant, JAMAIS « € », « EUR » ou « euro ».\n"
    "RÈGLE STRICTE : tout montant que tu écris doit apparaître EXACTEMENT "
    "dans les Données ou le Calcul fournis. Il est INTERDIT d'inventer, "
    "d'estimer ou de déduire un montant absent de ces blocs. Si le montant "
    "demandé n'y figure pas, indique simplement qu'il n'est pas disponible.\n"
    "N'affiche JAMAIS de JSON brut ni de tableau de données.\n"
    "Si un montant de retrait est demandé, indique les bornes autorisées et "
    "précise : « ce calcul est indicatif et ne constitue pas une autorisation ; "
    "le traitement réel est effectué par l'assureur »."
)

_COMPOSER_SYSTEM_AR = (
    "أنت مستشار عملاء شركة التأمين \"HAYETT 2000\". أجب بالعربية، بعنوان قصير "
    "بخط عريض ثم بنقاط موجزة.\n"
    "استخدم حصرياً البيانات المقدمة — لا تخترع أي رقم.\n"
    "جميع المبالغ بالدينار التونسي: اكتب دائمًا «دينار» بعد المبلغ، أبدًا "
    "«€» أو «يورو» أو «EUR».\n"
    "قاعدة صارمة: يجب أن يظهر أي مبلغ تكتبه بالضبط في البيانات أو الحساب "
    "المقدَّمين. يُمنع منعاً باتاً اختلاق أو تقدير أو استنتاج مبلغ غير موجود "
    "في هذين القسمين. إذا لم يظهر المبلغ المطلوب فيهما، قل ببساطة أنه غير متوفر.\n"
    "لا تعرض أبداً JSON خام أو جدول بيانات.\n"
    "إذا طلب المستخدم مبلغ انسحاب، اذكر الحدود المسموحة ووضح: «هذا الحساب "
    "تقديري ولا يشكل تفويضاً؛ المعالجة الفعلية تقوم بها شركة التأمين»."
)

_COMPOSER_USER = (
    "Question: {question}\n\n"
    "Données du client (ne pas coller ce bloc brut dans la réponse):\n"
    "{data}\n\n"
    "Calcul éventuel:\n{calculation}\n\n"
    "Rédige la réponse dans la langue de la question, sans JSON."
)


def _looks_like_bad_output(text: str) -> bool:
    if not text or not text.strip():
        return True
    s = text.strip()
    low = s.lower()
    if any(m in low for m in ("données du client", "donnees du client", "données (ne pas")):
        return True
    if s[0] in "{[":
        return True
    if s.count('":') >= 3 or s.count("':") >= 3:
        return True
    return False


_REFUSAL_PATTERNS_FR = ("non disponible", "pas disponible", "indisponible")
_REFUSAL_PATTERNS_AR = ("غير متوفر", "غير متاح", "غير موجود")


def _answer_claims_unavailable(answer: str, data: list) -> bool:
    """True when the LLM claims the requested figure is unavailable although
    Oracle returned non-empty rows (the information IS in the payload).

    Fires only when data is non-empty; the empty-data path never reaches the
    LLM, so a genuine "unavailable" case never trips this guard.
    """
    if not data:
        return False
    s = str(answer or "")
    if any(p in s.lower() for p in _REFUSAL_PATTERNS_FR):
        return True
    norm = normalize_arabic(s)
    return any(p in norm for p in _REFUSAL_PATTERNS_AR)


_CURRENCY_RE = re.compile(r"€|\bEUR\b|\beuro(s)?\b", re.IGNORECASE)


def _answer_uses_wrong_currency(answer: str) -> bool:
    """True when an LLM-composed answer writes an amount in euros (€/EUR/euro).

    Financial answers must use Tunisian dinars ('DT' / 'دينار'), never euros.
    Oracle amounts are the source of truth and are denominated in dinars.
    """
    return bool(_CURRENCY_RE.search(str(answer or "")))


def _fallback_answer(intent: str, data: list, calculation: dict, language: str) -> str:
    fr = language != "ar"
    label_prime = "Prime" if fr else "القسط"
    if intent == "epargne":
        if not data:
            return (
                "Aucune épargne enregistrée pour vos contrats."
                if fr else "لا يوجد ادخار مسجل لعقودك."
            )
        total_epargne = sum(_as_float(r.get("montant_epargne")) or 0 for r in data)
        total_part = sum(_as_float(r.get("participation_benefices")) or 0 for r in data)
        lines = (
            [f"Votre épargne actuelle est de {_fmt_grouped(total_epargne)} DT."]
            if fr else
            [f"ادخارك الحالي هو {_fmt_grouped(total_epargne)} دينار."]
        )
        if total_part:
            lines.append(
                f"Participation aux bénéfices : {_fmt_grouped(total_part)} DT."
                if fr else
                f"المساهمة في الأرباح: {_fmt_grouped(total_part)} دينار."
            )
        return "\n".join(lines)
    if intent == "versements_annee":
        total = sum(_as_float(r.get("total_verse")) or 0 for r in data)
        year = calculation.get("annee", date.today().year)
        return (
            f"Vous avez versé {_fmt_grouped(total)} DT en {year}."
            if fr else
            f"لقد سددت {_fmt_grouped(total)} دينار سنة {year}."
        )
    if intent == "retrait":
        if calculation.get("epargne_totale") is None:
            return (
                "Aucune épargne enregistrée pour vos contrats."
                if fr else "لا يوجد ادخار مسجل لعقودك."
            )
        lines = (
            ["**Retrait partiel (indicatif)**"]
            if fr else ["**الانسحاب المبكر (تقديري)**"]
        )
        if fr:
            lines.append(f"- Épargne totale : {_fmt_grouped(calculation['epargne_totale'])} DT")
            lines.append(f"- Minimum (10 %) : {_fmt_grouped(calculation['min_10_pourcent'])} DT")
            lines.append(f"- Maximum (75 %) : {_fmt_grouped(calculation['max_75_pourcent'])} DT")
            if "montant_demande" in calculation:
                verdict = "ce montant est dans la fourchette autorisée" if calculation["autorisable"] \
                    else "ce montant est hors de la fourchette autorisée"
                lines.append(f"- Montant demandé ({_fmt_grouped(calculation['montant_demande'])} DT) : {verdict}.")
        else:
            lines.append(f"- إجمالي الادخار : {_fmt_grouped(calculation['epargne_totale'])} دينار")
            lines.append(f"- الحد الأدنى (10٪) : {_fmt_grouped(calculation['min_10_pourcent'])} دينار")
            lines.append(f"- الحد الأقصى (75٪) : {_fmt_grouped(calculation['max_75_pourcent'])} دينار")
            if "montant_demande" in calculation:
                verdict = "المبلغ ضمن النطاق المسموح" if calculation["autorisable"] \
                    else "المبلغ خارج النطاق المسموح"
                lines.append(f"- المبلغ المطلوب ({_fmt_grouped(calculation['montant_demande'])} دينار) : {verdict}.")
        lines.append(
            "Ce calcul est indicatif et ne constitue pas une autorisation ; "
            "le traitement réel est effectué par l'assureur."
            if fr else
            "هذا الحساب تقديري ولا يشكل تفويضاً؛ المعالجة الفعلية تقوم بها شركة التأمين."
        )
        return "\n".join(lines)
    if intent == "beneficiaire":
        if not data:
            return (
                "Aucun bénéficiaire enregistré."
                if fr else "لا يوجد مستفيد مسجل."
            )
        lines = ["**Bénéficiaires**" if fr else "**المستفيدون**"]
        for row in data:
            lines.append(
                f"- Contrat n°{row['contrat_id']} : {row.get('prenom', '')} {row.get('nom', '')} "
                f"({row.get('relation', '')})"
                if fr else
                f"- العقد رقم {row['contrat_id']} : {row.get('prenom', '')} {row.get('nom', '')} "
                f"({row.get('relation', '')})"
            )
        return "\n".join(lines)
    if intent == "prime":
        if not data:
            return (
                "Aucun contrat enregistré."
                if fr else "لا يوجد عقد مسجل."
            )
        if len(data) == 1:
            amount = _fmt_grouped(data[0].get("montant_prime"))
            return (
                f"Votre prime mensuelle est de {amount} DT."
                if fr else
                f"قسطك الشهري هو {amount} دينار."
            )
        lines = []
        for row in data:
            lines.append(
                f"- Contrat n°{row['contrat_id']} : "
                f"{_fmt_grouped(row['montant_prime'])} DT / "
                f"{row.get('periodicite', '') or 'Mensuel'}"
                if fr else
                f"- العقد رقم {row['contrat_id']} : "
                f"{_fmt_grouped(row['montant_prime'])} دينار شهرياً"
            )
        return "\n".join(lines)
    # contrat / default
    if not data:
        return (
            "Aucun contrat enregistré."
            if fr else "لا يوجد عقد مسجل."
        )
    lines = ["**Mes contrats**" if fr else "**عقودي**"]
    for row in data:
        lines.append(
            f"- Contrat n°{row['contrat_id']} : {label_prime} "
            f"{_fmt_grouped(row['montant_prime'])} DT / {row.get('periodicite', '') or 'Mensuel'} — "
            f"statut {row.get('statut', '')}"
            if fr else
            f"- العقد رقم {row['contrat_id']} : {label_prime} "
            f"{_fmt_grouped(row['montant_prime'])} دينار شهرياً — الحالة {row.get('statut', '')}"
        )
    return "\n".join(lines)


def run_personal_analytics(query: str, client_id: int, intent: str = None) -> dict:
    """Controlled Oracle analytics for the authenticated client.

    Returns {intent, data, calculation, answer, language, requires_login}.
    Raises RuntimeError(DatabaseUnavailable) when Oracle is down.

    Anti-hallucination rules:
    - Oracle down            -> DatabaseUnavailable raised BEFORE any LLM call.
    - Oracle returns empty   -> deterministic answer only, the LLM is never
                                called with an empty payload.
    - intent versements_annee, epargne, prime -> 100 % deterministic (pure
                                factual lookups): the LLM is never called,
                                even with rows. The composer stays available
                                for the narrative intents (retrait, contrat,
                                beneficiaire).
    - LLM composed answer    -> validated: rejected when it invents a figure
                                absent from the Oracle result (_answer_invents_money),
                                or when it claims the figure is unavailable
                                although Oracle returned rows
                                (_answer_claims_unavailable), or when it writes
                                an amount in euros (_answer_uses_wrong_currency):
                                financial answers are always in 'DT'/'دينار'.
                                On rejection the deterministic fallback answer
                                is used.
    """
    language = detect_language(query)
    intent = intent or classify_personal_intent(query)

    data, calculation = _intent_data(intent, client_id, query)

    total_oracle = None
    if intent == "versements_annee":
        total_oracle = sum(_as_float(r.get("total_verse")) or 0 for r in data)
    logger.debug(
        "[PERSONAL] client_id=%s intent=%s year=%s rows=%d total_oracle=%s",
        client_id,
        intent,
        calculation.get("annee") if isinstance(calculation, dict) else None,
        len(data),
        total_oracle,
    )

    # 1) Oracle returned no rows, or this is a pure-factual intent
    #    (versements_annee, epargne, prime): deterministic answer only,
    #    the LLM is never called.
    if not data or intent in ("versements_annee", "epargne", "prime"):
        answer = _fallback_answer(intent, data, calculation, language)

    else:
        data_json = str({"rows": data, "calcul": calculation})
        if len(data_json) > 2000:
            data_json = data_json[:2000] + " ...(truncated)"

        try:
            system = _COMPOSER_SYSTEM_AR if language == "ar" else _COMPOSER_SYSTEM_FR
            chain = (
                ChatPromptTemplate.from_messages([
                    ("system", system),
                    ("user", _COMPOSER_USER),
                ])
                | llm_client
                | StrOutputParser()
            )
            answer = chain.invoke({
                "question": query,
                "data": data_json,
                "calculation": str(calculation) if calculation else "-",
            }).strip()
            if (_looks_like_bad_output(answer)
                    or _answer_invents_money(answer, data, calculation)
                    or _answer_claims_unavailable(answer, data)
                    or _answer_uses_wrong_currency(answer)):
                answer = None
            if answer is None:
                answer = _fallback_answer(intent, data, calculation, language)
        except Exception as e:
            logger.warning(f"[PERSONAL] Answer composer failed, fallback used: {e}")
            answer = _fallback_answer(intent, data, calculation, language)

    return {
        "intent": intent,
        "data": data,
        "calculation": calculation,
        "answer": answer,
        "language": language,
        "requires_login": False,
    }