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

sys.modules["models"] = _mock_models
