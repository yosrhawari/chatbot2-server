from ingest import chapter_hint, chapter_reference, fix_arabic_text, normalize_arabic_query

# Chapter 9 line exactly as pypdf extracts it from the Arabic PDF:
# visual order + presentation forms. Fixture built with \u escapes so the
# shaped codepoints survive source encoding round-trips.
_REVERSED_CHAPTER_9 = (
    "\u0627\ufedf\ufecc\ufed8\ufeaa"            # العقد (visual order)
    "  \ufecb\ufc86"                            # على
    "  \ufe97\ufeb4\ufe92\ufef4\ufed8\ufe8e\u062a."  # تسبيقات. (visual)
    "  9 \u0627\ufedf\ufed4\ufebc\ufede"        # الفصل (visual)
)


class TestFixArabicText:
    def test_reverses_visual_order_and_normalises_forms(self):
        fixed = fix_arabic_text(_REVERSED_CHAPTER_9)
        assert fixed == "الفصل 9 تسبيقات. على العقد"

    def test_leaves_french_untouched(self):
        text = "ARTICLE 9. AVANCES SUR CONTRAT\nHAYETT verse les sommes."
        assert fix_arabic_text(text) == text

    def test_leaves_logical_arabic_untouched(self):
        text = "الفصل 9 تسبيقات على العقد"
        assert fix_arabic_text(text) == text

    def test_short_arabic_line_below_threshold_untouched(self):
        text = "a 9 b"
        assert fix_arabic_text(text) == text

    def test_multi_line_mixed_document(self):
        text = "TITRE 1\n" + _REVERSED_CHAPTER_9 + "\nFIN DE PAGE"
        fixed = fix_arabic_text(text)
        assert fixed.splitlines()[0] == "TITRE 1"
        assert fixed.splitlines()[1] == "الفصل 9 تسبيقات. على العقد"
        assert fixed.splitlines()[2] == "FIN DE PAGE"

    def test_empty_and_short_input(self):
        assert fix_arabic_text("") == ""
        assert fix_arabic_text("اب") == "اب"


class TestNormalizeArabicQuery:
    def test_strips_diacritics_and_tatweel(self):
        assert normalize_arabic_query("\u0627\u064f\u0644\u0642\u064e\u0633\u0637") == "القسط"

    def test_unifies_alef_variants(self):
        assert normalize_arabic_query("أقساط إيقاف آجل") == "اقساط ايقاف اجل"

    def test_unifies_ta_marbuta_and_alef_maqsura(self):
        assert normalize_arabic_query("حياة على") == "حياه علي"

    def test_arabic_indic_digits_to_ascii(self):
        assert normalize_arabic_query("الفصل ٩") == "الفصل 9"

    def test_collapses_whitespace(self):
        assert normalize_arabic_query("  الفصل   9  ") == "الفصل 9"

    def test_french_left_intact(self):
        assert normalize_arabic_query("chapitre 9, avances sur contrat") == "chapitre 9, avances sur contrat"


class TestChapterReference:
    def test_canonical_space_form(self):
        assert chapter_reference("الفصل 9") == "الفصل 9"

    def test_glued_digit(self):
        assert chapter_reference("الفصل9 1") == "الفصل 9"

    def test_article(self):
        assert chapter_reference("المادة 5") == "المادة 5"

    def test_french_returns_empty(self):
        assert chapter_reference("chapitre 9 avances sur contrat") == ""

    def test_no_reference_returns_empty(self):
        assert chapter_reference("quelles sont les avances ?") == ""


class TestChapterHint:
    def test_arabic_chapter_number(self):
        assert chapter_hint("الفصل 9") == "chapitre 9 article 9"

    def test_arabic_chapter_no_space(self):
        assert chapter_hint("الفصل9 1") == "chapitre 9 article 9"

    def test_normalized_arabic_article(self):
        assert "chapitre 5" in chapter_hint("المادة 5")

    def test_french_query_no_hint(self):
        assert chapter_hint("chapitre 9 avances sur contrat") == ""

    def test_no_chapter_reference_no_hint(self):
        assert chapter_hint("quelles sont les avances ?") == ""