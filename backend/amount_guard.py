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
    the data payload. Percentages and calendar years are ignored."""
    allowed = _authorized_amounts(data, calculation)
    s = str(answer or "")
    for match in _AMOUNT_RE.finditer(s):
        token = match.group(0)
        if not token:
            continue
        # Percentages (10 %, 75%) are policy bounds, not client amounts.
        after = s[match.end():match.end() + 3].lstrip()
        if after.startswith("%"):
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
