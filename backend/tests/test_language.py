from language import detect_language, has_arabic, normalize_arabic


class TestHasArabic:
    def test_arabic_characters(self):
        assert has_arabic("ما هي شروط الانسحاب؟") is True

    def test_french_only(self):
        assert has_arabic("Quelles sont les conditions du retrait ?") is False

    def test_empty(self):
        assert has_arabic("") is False


class TestDetectLanguage:
    def test_french_question(self):
        assert detect_language("Quelles sont les conditions du retrait ?") == "fr"

    def test_arabic_question(self):
        assert detect_language("ما هي شروط الانسحاب؟") == "ar"

    def test_short_arabic_no_langdetect(self):
        assert detect_language("الادخار") == "ar"

    def test_empty_defaults_to_french(self):
        assert detect_language("") == "fr"

    def test_whitespace_defaults_to_french(self):
        assert detect_language("   ") == "fr"

    def test_french_kept_french(self):
        assert detect_language("Quelle est la durée de la phase d'épargne ?") == "fr"


class TestNormalizeArabic:
    def test_diacritics_and_tatweel(self):
        assert normalize_arabic("\u0627\u064f\u0644\u0642\u064e\u0633\u0637") == "القسط"

    def test_unifies_alef_variants(self):
        assert normalize_arabic("أقساط إيقاف آجل") == "اقساط ايقاف اجل"

    def test_ta_marbuta_and_alef_maqsura(self):
        assert normalize_arabic("حياة على") == "حياه علي"

    def test_indic_digits(self):
        assert normalize_arabic("الفصل ٩") == "الفصل 9"

    def test_collapses_whitespace(self):
        assert normalize_arabic("  الفصل   9  ") == "الفصل 9"

    def test_french_untouched(self):
        assert normalize_arabic("Quel retrait ?") == "Quel retrait ?"
