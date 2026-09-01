import sys
from unittest.mock import MagicMock

_mock_models = MagicMock()
_mock_models.llm_client = MagicMock()
_mock_models.embedding_function = MagicMock()
_mock_models.reranker_compressor = MagicMock()
_mock_models.semantic_cache = MagicMock()
_mock_models.CrossEncoderReranker = MagicMock
_mock_models.E5Embeddings = MagicMock
_mock_models.SemanticCache = MagicMock
_mock_models.DEVICE = "cpu"

_mock_database = MagicMock()
_mock_database.find_account_by_email = MagicMock()
_mock_database.find_client_by_id = MagicMock()
_mock_database.DatabaseUnavailable = Exception
_mock_database._connect = MagicMock()
_mock_database.init_db = MagicMock(return_value=True)
_mock_database.db_available = MagicMock(return_value=True)

sys.modules["models"] = _mock_models
sys.modules["database"] = _mock_database
