import importlib.util
from pathlib import Path

# Load real rag module to be immune to sys.modules stubs from test_api_auth
_spec = importlib.util.spec_from_file_location("real_rag", Path(__file__).parent.parent / "rag.py")
_real_rag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real_rag)

is_standalone_query = _real_rag.is_standalone_query
condense_query = _real_rag.condense_query


def test_is_standalone_query_positive():
    assert is_standalone_query("Puis-je effectuer un versement exceptionnel ?") is True
    assert is_standalone_query("Comment effectuer un rachat total ?") is True
    assert is_standalone_query("Quelles sont les conditions de l'avance sur contrat ?") is True
    assert is_standalone_query("Comment désigner un bénéficiaire ?") is True
    assert is_standalone_query("ما هي شروط تسبقة على العقد ؟") is True
    assert is_standalone_query("هل يمكن القيام بتسبيق ؟") is True


def test_is_standalone_query_negative_followup():
    assert is_standalone_query("Et pour son montant ?") is False
    assert is_standalone_query("Et si je veux le faire maintenant ?") is False
    assert is_standalone_query("Comment faire ?") is False
    assert is_standalone_query("Quel est le délai ?") is False
    assert is_standalone_query("Combien ?") is False
    assert is_standalone_query("وکيف يتم ذلك؟") is False


def test_condense_query_bypasses_standalone():
    history = "User: Quelles sont les garanties décès ?\nAssistant: Les garanties décès couvrent..."
    q = "Puis-je effectuer un versement exceptionnel ?"
    res = condense_query(q, history)
    assert res == q
