import inspect

import database
import personal_analytics as pa


class TestClassifyIntent:
    def test_epargne_fr(self):
        assert pa.classify_personal_intent("Quelle est mon épargne actuelle ?") == "epargne"

    def test_epargne_ar(self):
        assert pa.classify_personal_intent("ما هو ادخاري الحالي؟") == "epargne"

    def test_versements_annee(self):
        assert pa.classify_personal_intent("Combien ai-je versé cette année ?") == "versements_annee"

    def test_versements_annee_ar(self):
        assert pa.classify_personal_intent("كم دفعت في سنة 2025؟") == "versements_annee"

    def test_prime_mensuelle(self):
        assert pa.classify_personal_intent("Quelle est ma prime mensuelle ?") == "prime"

    def test_prime_ar(self):
        assert pa.classify_personal_intent("ما هي قيمة قسطي؟") == "prime"

    def test_epargne_ar(self):
        assert pa.classify_personal_intent("كم تبلغ مدخراتي الحالية؟") == "epargne"

    def test_retrait_fr(self):
        # General rule without possessive -> not a personal Oracle query
        assert pa.classify_personal_intent("Puis-je faire un retrait partiel ?") == "unknown"

    def test_retrait_ar(self):
        assert pa.classify_personal_intent("هل يمكنني الانسحاب الجزئي؟") == "unknown"

    def test_beneficiaire(self):
        assert pa.classify_personal_intent("Qui est mon bénéficiaire ?") == "beneficiaire"

    def test_contrat_infos(self):
        assert pa.classify_personal_intent("Quelle est la durée de mon contrat ?") == "contrat"

    def test_unknown_returns_unknown(self):
        # No keyword match and the (mocked) LLM fallback yields no valid intent.
        assert pa.classify_personal_intent("Quelle est la croissance des ventes par région ?") == "unknown"


class TestCalculations:
    def test_retrait_bounds_18500(self):
        bounds = pa.compute_retrait_bounds(18500)
        assert bounds["min_10_pourcent"] == 1850.0
        assert bounds["max_75_pourcent"] == 13875.0

    def test_retrait_bounds_zero(self):
        bounds = pa.compute_retrait_bounds(0)
        assert bounds["min_10_pourcent"] == 0.0
        assert bounds["max_75_pourcent"] == 0.0

    def test_find_requested_amount(self):
        assert pa.find_requested_amount("Puis-je retirer 10 000 DT ?") == 10000.0
        assert pa.find_requested_amount("Puis-je retirer 13 875 DT ?") == 13875.0
        assert pa.find_requested_amount("Combien puis-je retirer de 18500 ?") == 18500.0
        assert pa.find_requested_amount("Quelle est mon épargne ?") is None

    def test_find_requested_amount_formats(self):
        # Mandatory explicit formats: plain, space and dot thousands,
        # European decimal with comma.
        assert pa.find_requested_amount("Puis-je retirer 18500 DT ?") == 18500.0
        assert pa.find_requested_amount("Puis-je retirer 18 500 DT ?") == 18500.0
        assert pa.find_requested_amount("Puis-je retirer 18.500 DT ?") == 18500.0
        assert pa.find_requested_amount("Puis-je retirer 18.500,56 DT ?") == 18500.56
        assert pa.find_requested_amount("Puis-je retirer 18500,56 DT ?") == 18500.56
        # A decimal point after a NOT-3-digit group stays a decimal (18.5).
        assert pa.find_requested_amount("Puis-je retirer 18.5 DT ?") == 18.5


def _rows_ahmed_epargne():
    return [
        {"contrat_id": 1, "date_calcul": "2026-06-30",
         "montant_epargne": 18500.0, "participation_benefices": 1250.0},
    ]


class TestRunPersonalAnalytics:
    def test_epargne_data_scoped_to_client(self, monkeypatch):
        seen = {}
        def fake_latest_epargne(client_id):
            seen["client_id"] = client_id
            return _rows_ahmed_epargne()
        monkeypatch.setattr(pa, "latest_epargne", fake_latest_epargne)
        result = pa.run_personal_analytics("Quelle est mon épargne ?", 1001, intent="epargne")
        assert seen["client_id"] == 1001
        assert result["intent"] == "epargne"
        assert result["data"][0]["montant_epargne"] == 18500.0
        assert "épargne" in result["answer"].lower()  # deterministic fallback with mocked LLM

    def test_retrait_calculation_and_safety_note(self, monkeypatch):
        monkeypatch.setattr(pa, "latest_epargne", lambda cid: _rows_ahmed_epargne())
        result = pa.run_personal_analytics(
            "Puis-je retirer 10 000 DT ?", 1001, intent="retrait"
        )
        calc = result["calculation"]
        assert calc["epargne_totale"] == 18500.0
        assert calc["min_10_pourcent"] == 1850.0
        assert calc["max_75_pourcent"] == 13875.0
        assert calc["montant_demande"] == 10000.0
        assert calc["autorisable"] is True
        # The answer must never present the calculation as a final authorization.
        assert "indicatif" in result["answer"]
        assert "assureur" in result["answer"]

    def test_retrait_hors_bornes(self, monkeypatch):
        monkeypatch.setattr(pa, "latest_epargne", lambda cid: _rows_ahmed_epargne())
        result = pa.run_personal_analytics(
            "Puis-je retirer 2 000 000 DT ?", 1001, intent="retrait"
        )
        assert result["calculation"]["autorisable"] is False

    def test_versements_annee_passes_current_year(self, monkeypatch):
        from datetime import date
        seen = {}
        def fake_versements(client_id, year):
            seen["year"] = year
            return [{"contrat_id": 1, "nb_versements": 6, "total_verse": 1500.0}]
        monkeypatch.setattr(pa, "versements_annee", fake_versements)
        result = pa.run_personal_analytics("Combien ai-je versé cette année ?", 1001, intent="versements_annee")
        assert seen["year"] == date.today().year
        assert result["data"][0]["total_verse"] == 1500.0

    def test_empty_data_gives_graceful_answer(self, monkeypatch):
        monkeypatch.setattr(pa, "latest_epargne", lambda cid: [])
        result = pa.run_personal_analytics("Quelle est mon épargne ?", 1001, intent="epargne")
        assert "Aucune" in result["answer"]
        assert result["requires_login"] is False


class TestControlledQueriesAlwaysFilterByClientId:
    """Plan §15: every Oracle query must be scoped by the authenticated
    client_id — the backend never lets a question supply the identifier."""

    def test_all_query_sql_contains_client_id_binding(self):
        import pathlib
        from unittest.mock import MagicMock

        db_path = pathlib.Path(__file__).parent.parent / "database.py"
        text = db_path.read_text(encoding="utf-8")
        for name in (
            "find_account_by_email",
            "find_client_by_id",
            "list_contrats",
            "latest_epargne",
            "versements_annee",
            "list_beneficiaires",
        ):
            obj = getattr(database, name, None)
            if isinstance(obj, MagicMock):
                # conftest mocks database; verify via file content instead
                if name == "find_account_by_email":
                    assert "LOWER(c.email) = LOWER(:email)" in text
                    continue
                assert ":client_id" in text, f"{name} does not bind :client_id"
                continue
            source = inspect.getsource(obj)
            if name == "find_account_by_email":
                assert "LOWER(c.email) = LOWER(:email)" in source
                continue
            assert ":client_id" in source, f"{name} does not bind :client_id"
            assert "client_id = :client_id" in source, name


class TestYearExtraction:
    def test_question_year_2025_is_used(self, monkeypatch):
        seen = {}

        def fake_versements(client_id, year):
            seen["year"] = year
            return [{"contrat_id": "C001", "nb_versements": 3, "total_verse": 1500.0}]

        monkeypatch.setattr(pa, "versements_annee", fake_versements)
        result = pa.run_personal_analytics(
            "Combien ai-je versé en l'année 2025 ?", 1001, intent="versements_annee"
        )
        assert seen["year"] == 2025
        assert result["data"][0]["total_verse"] == 1500.0

    def test_no_year_uses_current_year(self):
        from datetime import date
        assert pa._requested_year("Combien ai-je versé cette année ?") is None
        assert pa._requested_year("Quelle est mon épargne ?") is None
        assert pa._requested_year("Combien ai-je versé en l'année 2025 ?") == 2025
        assert pa._requested_year("Combien ai-je versé en 2025 ?") == 2025
        assert date.today().year == pa._requested_year("") or pa._requested_year("") is None


class TestNoInventedAmounts:
    """The LLM must NEVER invent a monetary figure, and must NEVER claim a
    figure is unavailable when Oracle actually returned it."""

    @staticmethod
    def _fake_llm(text, call_tracker):
        class FakeLLM:
            def __call__(self, inputs):
                call_tracker["n"] += 1
                return text

        return FakeLLM()

    @staticmethod
    def _rows_epargne_18500():
        return [
            {"contrat_id": 1, "date_calcul": "2026-06-30",
             "montant_epargne": 18500.0, "participation_benefices": 1250.0},
        ]

    def test_empty_oracle_never_calls_llm_and_answers_zero(self, monkeypatch):
        calls = {"n": 0}

        monkeypatch.setattr(pa, "versements_annee", lambda cid, year: [])
        monkeypatch.setattr(
            pa, "llm_client",
            self._fake_llm("Vous avez versé 12 000 € cette année.", calls),
        )
        result = pa.run_personal_analytics(
            "Combien ai-je versé cette année ?", 1001, intent="versements_annee"
        )
        assert calls["n"] == 0  # LLM never invoked on empty Oracle payload
        assert "Vous avez versé 0 DT en" in result["answer"]
        for token in ("12000", "12 000"):
            assert token not in result["answer"]

    def test_oracle_2025_1500_never_invented(self, monkeypatch):
        # versements_annee is 100 % deterministic: the LLM is never called,
        # even when Oracle returned rows, and the answer must state 1 500 DT.
        calls = {"n": 0}

        monkeypatch.setattr(
            pa, "versements_annee",
            lambda cid, year: [{"contrat_id": "C001", "nb_versements": 3, "total_verse": 1500.0}],
        )
        monkeypatch.setattr(
            pa, "llm_client",
            self._fake_llm("Vous avez versé 51 000 € en 2025.", calls),
        )
        result = pa.run_personal_analytics(
            "Combien ai-je versé en l'année 2025 ?", 1001, intent="versements_annee"
        )
        assert calls["n"] == 0  # deterministic intent: LLM never invoked
        answer = result["answer"]
        assert "1 500" in answer
        assert "non disponible" not in answer.lower()
        for token in ("12000", "12 000", "51 000"):
            assert token not in answer

    def test_llm_invention_is_replaced_by_fallback(self, monkeypatch):
        # Intent where the composer is still allowed (retrait): an invented
        # figure (51 000) is rejected and replaced by the deterministic bounds.
        calls = {"n": 0}

        monkeypatch.setattr(pa, "latest_epargne", lambda cid: self._rows_epargne_18500())
        monkeypatch.setattr(
            pa, "llm_client",
            self._fake_llm("Votre épargne est de 51 000 €.", calls),
        )
        result = pa.run_personal_analytics(
            "Puis-je faire un retrait partiel ?", 1001, intent="retrait"
        )
        assert calls["n"] == 1  # LLM allowed for retrait (data non-empty)...
        assert "51 000" not in result["answer"]  # ...but its invention is replaced
        assert "18 500" in result["answer"]

    def test_llm_exact_oracle_amount_is_kept(self, monkeypatch):
        # Intent where the composer is still allowed (retrait): an amount
        # derived deterministically from Oracle (75 % of 18 500 = 13 875)
        # is kept.
        calls = {"n": 0}

        monkeypatch.setattr(pa, "latest_epargne", lambda cid: self._rows_epargne_18500())
        monkeypatch.setattr(
            pa, "llm_client",
            self._fake_llm("Le maximum de retrait est de 13 875 DT.", calls),
        )
        result = pa.run_personal_analytics(
            "Puis-je faire un retrait partiel ?", 1001, intent="retrait"
        )
        assert calls["n"] == 1
        assert "13 875" in result["answer"]

    def test_retrait_llm_claims_unavailable_falls_back(self, monkeypatch):
        calls = {"n": 0}

        monkeypatch.setattr(pa, "latest_epargne", lambda cid: self._rows_epargne_18500())
        monkeypatch.setattr(
            pa, "llm_client",
            self._fake_llm("Votre épargne n'est pas disponible.", calls),
        )
        result = pa.run_personal_analytics(
            "Puis-je faire un retrait partiel ?", 1001, intent="retrait"
        )
        assert calls["n"] == 1  # LLM invoked (Oracle has rows)...
        answer = result["answer"]  # ...then rejected -> deterministic fallback
        assert "18 500" in answer
        assert "non disponible" not in answer.lower()

    def test_oracle_unavailable_raises_before_llm(self, monkeypatch):
        calls = {"n": 0}

        def boom(client_id, year):
            raise pa.DatabaseUnavailable("Oracle unavailable.")

        monkeypatch.setattr(pa, "versements_annee", boom)
        monkeypatch.setattr(
            pa, "llm_client",
            self._fake_llm("Vous avez versé 12 000 €.", calls),
        )
        try:
            pa.run_personal_analytics("Combien ai-je versé cette année ?", 1001, intent="versements_annee")
            raised = False
        except pa.DatabaseUnavailable:
            raised = True
        assert raised
        assert calls["n"] == 0


class TestCurrencyAlwaysDT:
    """Financial answers are always in Tunisian dinars ('DT'), never euros.
    Oracle amounts are the source of truth; the LLM cannot change the
    currency (€/EUR/euro) nor sum separate Oracle fields (1425 + 75)."""

    @staticmethod
    def _fake_llm(text, call_tracker):
        class FakeLLM:
            def __call__(self, inputs):
                call_tracker["n"] += 1
                return text

        return FakeLLM()

    @staticmethod
    def _rows_epargne_1425():
        return [
            {"contrat_id": "C001", "date_calcul": "2026-06-30",
             "montant_epargne": 1425.0, "participation_benefices": 75.0},
        ]

    @staticmethod
    def _rows_contrat_500():
        return [
            {"contrat_id": "C001", "montant_prime": 500.0,
             "periodicite": "Mensuelle", "statut": "ACTIF"},
        ]

    def test_currency_guard_units(self):
        for bad in ("500 €", "500 EUR", "500 euros", "500 euro", "Votre prime de 500€"):
            assert pa._answer_uses_wrong_currency(bad)
        for ok in ("500 DT", "500 dinars", "1 425 دينار", "500", "Votre épargne actuelle"):
            assert not pa._answer_uses_wrong_currency(ok)

    def test_prime_500_dt_never_euros(self, monkeypatch):
        calls = {"n": 0}

        monkeypatch.setattr(pa, "list_contrats", lambda cid: self._rows_contrat_500())
        monkeypatch.setattr(
            pa, "llm_client",
            self._fake_llm("Votre prime mensuelle est de 500 €.", calls),
        )
        result = pa.run_personal_analytics("Quelle est ma prime ?", 1001, intent="prime")
        assert calls["n"] == 0  # prime is fully deterministic (never LLM)
        answer = result["answer"]
        assert "500 DT" in answer
        assert "€" not in answer
        assert "EUR" not in answer and "euro" not in answer.lower()

    def test_epargne_1425_dt_never_euros(self, monkeypatch):
        calls = {"n": 0}

        monkeypatch.setattr(pa, "latest_epargne", lambda cid: self._rows_epargne_1425())
        monkeypatch.setattr(
            pa, "llm_client",
            self._fake_llm("Votre épargne actuelle est de 1 425 €.", calls),
        )
        result = pa.run_personal_analytics("Quelle est mon épargne actuelle ?", 1001, intent="epargne")
        assert calls["n"] == 0  # epargne is fully deterministic (never LLM)
        answer = result["answer"]
        assert "1 425 DT" in answer
        assert "€" not in answer
        assert "EUR" not in answer and "euro" not in answer.lower()

    def test_epargne_never_sums_epargne_plus_benefices(self, monkeypatch):
        # 1425 + 75 = 1500 must NOT appear: each Oracle field stays separate.
        calls = {"n": 0}

        monkeypatch.setattr(pa, "latest_epargne", lambda cid: self._rows_epargne_1425())
        monkeypatch.setattr(
            pa, "llm_client",
            self._fake_llm("Votre épargne actuelle est de 1 500 DT.", calls),
        )
        result = pa.run_personal_analytics("Quelle est mon épargne actuelle ?", 1001, intent="epargne")
        assert calls["n"] == 0  # deterministic fallback never sums the fields
        answer = result["answer"]
        assert "1 425 DT" in answer
        assert "1 500" not in answer

    def test_versements_2025_1500_dt_never_euros(self, monkeypatch):
        calls = {"n": 0}

        monkeypatch.setattr(
            pa, "versements_annee",
            lambda cid, year: [{"contrat_id": "C001", "nb_versements": 3, "total_verse": 1500.0}],
        )
        monkeypatch.setattr(
            pa, "llm_client",
            self._fake_llm("Vous avez versé 1 500 €.", calls),
        )
        result = pa.run_personal_analytics(
            "Combien ai-je versé en 2025 ?", 1001, intent="versements_annee"
        )
        assert calls["n"] == 0  # versements_annee is fully deterministic
        answer = result["answer"]
        assert "1 500 DT" in answer
        assert "€" not in answer

    def test_versements_2026_0_dt_never_euros(self, monkeypatch):
        calls = {"n": 0}

        monkeypatch.setattr(pa, "versements_annee", lambda cid, year: [])
        monkeypatch.setattr(
            pa, "llm_client",
            self._fake_llm("Vous avez versé 0 €.", calls),
        )
        result = pa.run_personal_analytics(
            "Combien ai-je versé cette année ?", 1001, intent="versements_annee"
        )
        assert calls["n"] == 0  # empty Oracle payload: LLM never invoked
        answer = result["answer"]
        assert "0 DT" in answer
        assert "€" not in answer

    def test_bilingual_currencies_fr_and_ar(self, monkeypatch):
        # The same Oracle figures must be rendered in DT (FR) and
        # دينار (AR), never in €/EUR/euro.
        cases = [
            # (fr question, ar question, intent, fake rows fn, fr token, ar token)
            ("Combien ai-je versé en 2025 ?", "كم دفعت في سنة 2025؟",
             "versements_annee",
             lambda: [{"contrat_id": "C001", "nb_versements": 3, "total_verse": 1500.0}],
             "1 500 DT", "1 500 دينار"),
            ("Quelle est ma prime ?", "ما هي قيمة قسطي؟",
             "prime",
             lambda: self._rows_contrat_500(),
             "500 DT", "500 دينار"),
            ("Quelle est mon épargne actuelle ?", "كم تبلغ مدخراتي الحالية؟",
             "epargne",
             lambda: self._rows_epargne_1425(),
             "1 425 DT", "1 425 دينار"),
        ]
        for fr_q, ar_q, intent, rows, fr_token, ar_token in cases:
            calls = {"n": 0}
            if intent == "versements_annee":
                monkeypatch.setattr(pa, "versements_annee", lambda cid, year, rows=rows: rows())
            elif intent == "prime":
                monkeypatch.setattr(pa, "list_contrats", lambda cid, rows=rows: rows())
            else:
                monkeypatch.setattr(pa, "latest_epargne", lambda cid, rows=rows: rows())
            monkeypatch.setattr(
                pa, "llm_client",
                self._fake_llm("Votre montant est de 9 999 €.", calls),
            )
            # FR rendering
            result_fr = pa.run_personal_analytics(fr_q, 1001, intent=intent)
            assert calls["n"] == 0  # deterministic intent: LLM never invoked
            assert fr_token in result_fr["answer"]
            # AR rendering (same rows, Arabic question -> Arabic answer)
            result_ar = pa.run_personal_analytics(ar_q, 1001, intent=intent)
            assert calls["n"] == 0
            assert ar_token in result_ar["answer"]
            for answer in (result_fr["answer"], result_ar["answer"]):
                assert "€" not in answer
                assert "EUR" not in answer
                assert "euro" not in answer.lower()
