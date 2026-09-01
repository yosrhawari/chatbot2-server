from datetime import date

import pytest

from amount_guard import (
    _AMOUNT_RE,
    _YEAR_RE,
    _as_float,
    _parse_amount_token,
    _authorized_amounts,
    _answer_invents_money,
)


class TestParseAmountToken:
    @pytest.mark.parametrize(
        "token, expected",
        [
            ("18500", 18500.0),
            ("18 500", 18500.0),
            ("18.500", 18500.0),
            ("18.500,56", 18500.56),
            ("18500,56", 18500.56),
            ("18.5", 18.5),
        ],
    )
    def test_formats(self, token, expected):
        assert _parse_amount_token(token) == expected


class TestAsFloat:
    def test_none(self):
        assert _as_float(None) is None

    def test_bool_skipped(self):
        assert _as_float(True) is None

    def test_decimal(self):
        from decimal import Decimal
        assert _as_float(Decimal("12.34")) == 12.34

    def test_string_number(self):
        assert _as_float("42") == 42.0

    def test_string_text(self):
        assert _as_float("abc") is None


class TestAuthorizedAmounts:
    def test_oracle_rows(self):
        allowed = _authorized_amounts([{"montant": 1500.25}])
        assert 1500.25 in allowed

    def test_calculation_values(self):
        allowed = _authorized_amounts([], {"min_10_pourcent": 225.0})
        assert 225.0 in allowed

    def test_scalar_payload(self):
        assert 250.0 in _authorized_amounts({"total": 250})

    def test_nested_payload(self):
        assert 7.5 in _authorized_amounts([[{"a": 7.5}]])

    def test_row_count_allowed(self):
        allowed = _authorized_amounts([{}, {}, {}])
        assert 3.0 in allowed

    def test_zero_and_current_year_always_allowed(self):
        allowed = _authorized_amounts([])
        assert 0.0 in allowed
        assert float(date.today().year) in allowed


class TestAnswerInventsMoney:
    DATA = [{"total_verse": 18500.5}]

    def test_legit_amount_passes(self):
        answer = "Vous avez versé 18 500,5 DT au total."
        assert not _answer_invents_money(answer, self.DATA)

    def test_foreign_amount_rejected(self):
        answer = "Vous avez versé 12 000 DT au total."
        assert _answer_invents_money(answer, self.DATA)

    def test_percentages_exempt(self):
        assert not _answer_invents_money("Entre 10 % et 75 % de votre épargne.", [])

    def test_years_exempt(self):
        assert not _answer_invents_money("Situation de votre contrat en 2025.", [])

    def test_zero_exempt(self):
        assert not _answer_invents_money("Aucun versement : 0 DT cette année.", [])

    def test_row_count_passes(self):
        assert not _answer_invents_money("3 enregistrements trouvés.", [{}, {}, {}])

    def test_currency_not_the_guards_job(self):
        # Wrong-currency answers are caught by _answer_uses_wrong_currency;
        # this guard only rejects amounts absent from the payload.
        assert not _answer_invents_money("Total : 18500,5 €", self.DATA)

    def test_calculation_derived_amount_passes(self):
        calc = {"min_10_pourcent": 150.0, "max_75_pourcent": 1125.0}
        assert not _answer_invents_money(
            "Retrait possible entre 150 et 1 125 DT.", self.DATA, calc
        )

    def test_empty_answer_safe(self):
        assert not _answer_invents_money("", [])


class TestRegexes:
    def test_amount_re_finds_french_format(self):
        tokens = [m.group(0) for m in _AMOUNT_RE.finditer("entre 1 500,25 et 2 000 DT")]
        assert "1 500,25" in tokens
        assert "2 000" in tokens

    def test_year_re(self):
        assert _YEAR_RE.search("en 2025").group(1) == "2025"
        assert _YEAR_RE.search("en 199") is None
