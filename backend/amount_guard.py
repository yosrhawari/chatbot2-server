import logging

logger = logging.getLogger(__name__)

import re
from datetime import date
from decimal import Decimal


# ── Shared anti-hallucination guard ──────────────────────────────────────────
# LLM composers may invent a monetary figure (e.g. "12 000 €") that the data
# payload does not contain. Any amount in a composed answer that does not match
# a value present in the data source (Oracle rows / pandas result / computed
# calculation) is rejected so the caller can fall back to a deterministic
# summary. Percentages and calendar years are ignored (policy facts, not
# client amounts).
#
# Consumers:
#   - personal_analytics.py   (Oracle-backed personal answers)
#   - analytics.compose_analytics_answer (pandas/CSV results)
#   - router.handle_hybrid    (RAG + personal data synthesis)

def _as_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_AMOUNT_RE = re.compile(r"\d+(?:[\s.,]\d+)*")

_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _parse_amount_token(token: str) -> float | None:
    """Parse one amount token. Handles:
      18500     -> 18500.0    (plain integer)
      18 500    -> 18500.0    (space thousand separator)
      18.500    -> 18500.0    (dot thousand separator)
      18.500,56 -> 18500.56   (European: dot thousands, comma decimal)
      18500,56  -> 18500.56   (European: comma decimal)
      18.5      -> 18.5       (plain decimal)
    """
    token = re.sub(r"\s", "", token)
    if not token:
        return None
    if "," in token:
        # European notation: dots are thousand separators, comma is decimal.
        return float(token.replace(".", "").replace(",", "."))
    # A dot is a thousand separator only when followed by groups of 3 digits
    # (e.g. 18.500); otherwise it is a decimal point (e.g. 18.5, 3.14159).
    if "." in token and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
        return float(token.replace(".", ""))
    return float(token)


def _iter_numeric_leaves(payload):
    """Yield every numeric leaf of an arbitrary JSON-like payload.

    Unlike the original Oracle-specific version (which assumed list[dict]),
    this walks dicts, lists, tuples and bare scalars so pandas/CSV results of
    any shape can be checked by the same guard. Booleans are skipped."""
    if isinstance(payload, bool):
        return
    if isinstance(payload, dict):
        for v in payload.values():
            yield from _iter_numeric_leaves(v)
    elif isinstance(payload, (list, tuple, set)):
        for item in payload:
            yield from _iter_numeric_leaves(item)
    else:
        f = _as_float(payload)
        if f is not None:
            yield f


def _authorized_amounts(data=None, calculation=None) -> set:
    """Every exact numeric value the data source produced, rounded to cents."""
    allowed = {0.0, float(date.today().year)}
    for f in _iter_numeric_leaves(data):
        allowed.add(round(f, 2))
    for f in _iter_numeric_leaves(calculation):
        allowed.add(round(f, 2))
    # Row counts ("3 enregistrements") come from the SHAPE of the result,
    # not from a cell value; allow the top-level length too.
    if isinstance(data, (list, tuple)):
        allowed.add(float(len(data)))
    return allowed


def _answer_invents_money(answer: str, data=None, calculation=None) -> bool:
    """True when the composed answer contains a monetary figure absent from
    the data payload. Percentages, calendar years, article numbers, durations,
    and reference counts are ignored."""
    allowed = _authorized_amounts(data, calculation)
    s = str(answer or "")
    for match in _AMOUNT_RE.finditer(s):
        token = match.group(0)
        if not token:
            continue
        start, end = match.span()
        before = s[max(0, start - 20):start].lower()
        after = s[end:min(len(s), end + 20)].lower()

        # 1. Percentages (10 %, 75 %) are policy bounds, not money amounts
        if after.lstrip().startswith("%"):
            continue

        # 2. Article / section numbers (Article 10, Art. 9, Chapitre 3, الفصل 9, المادة 10)
        if re.search(r"(article|art\.?|chapitre|فصل|الفصل|مادة|المادة)\s*$", before):
            continue

        # 3. Contract IDs / reference numbers (Contrat n° 1, C001, n° 1, no 1)
        if re.search(r"(n°|no|numéro|numero|contrat|عقد)\s*$", before):
            continue

        # 4. Durations / time spans (2 ans, 3 années, 6 mois, 30 jours, 2 years, etc.)
        if re.match(r"^\s*(ans|an\b|années|annees|année|annee|mois|jours|jour|semaines|semaine|heures|heure|years|year|months|month|days|day|سنة|سنوات|أشهر|شهر|أيام|يوم)", after):
            continue

        # 5. Counts / occurrences (2 fois, 1 avance, 2 avances, 3 contrats, etc.)
        if re.match(r"^\s*(fois|avance|avances|contrat|contrats|retrait|retraits|تسبيق|تسبيقات|عقود|مرات|مرة)", after):
            continue

        # 6. Ordinals / bullet numbers (1., 2., 1er, 2ème, etc.)
        if re.match(r"^\s*(er|ère|ere|ème|eme|th|st|nd|rd)\b", after):
            continue
        if (start == 0 or s[start - 1] in "\n\r") and re.match(r"^\s*[.)-]", after):
            continue

        value = _parse_amount_token(token)
        if value is None or value == 0:
            continue
        # Calendar years (2026, date de souscription) are not amounts.
        if 1900 <= value <= 2100 and len(token.replace(" ", "").replace(".", "").replace(",", "")) == 4:
            continue
        if round(value, 2) not in allowed:
            logger.warning(
                f"[AMOUNT GUARD] Rejected LLM answer: foreign amount {value!r} "
                f"not in data result (allowed={sorted(allowed)})"
            )
            return True
    return False
