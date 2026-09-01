import logging

logger = logging.getLogger(__name__)

import re
import unicodedata

try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0
    _HAS_LANGDETECT = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_LANGDETECT = False

_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)

_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")
_ALEF_VARIANTS = str.maketrans("أإآٱ", "اااا")
_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _is_arabic_char(c: str) -> bool:
    return any(lo <= ord(c) <= hi for lo, hi in _ARABIC_RANGES)


def has_arabic(text) -> bool:
    """True if the text contains any Arabic character (base or shaped)."""
    return any(_is_arabic_char(c) for c in (text or ""))


def normalize_arabic(text: str) -> str:
    """Normalise Arabic text so keyword matching survives diacritics,
    alef variants and Arabic-Indic digits. Non-Arabic text is untouched."""
    s = unicodedata.normalize("NFKC", text or "")
    s = _DIACRITICS.sub("", s)
    s = s.translate(_ALEF_VARIANTS)
    s = s.translate(_INDIC_DIGITS)
    s = s.replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"\s+", " ", s).strip()


def detect_language(text) -> str:
    """Return 'ar' for Arabic text, 'fr' otherwise.

    The chatbot serves French and Arabic users (the two contract documents).
    Arabic is detected by character ranges (robust, no model needed); any
    other script defaults to French unless langdetect strongly disagrees.
    """
    t = (text or "").strip()
    if not t:
        return "fr"
    if has_arabic(t):
        return "ar"
    if _HAS_LANGDETECT and len(t) >= 20:
        try:
            lang = detect(t)
            if lang == "ar":
                return "ar"
        except Exception as e:
            logger.debug(f"[LANG] langdetect failed: {e}")
    return "fr"