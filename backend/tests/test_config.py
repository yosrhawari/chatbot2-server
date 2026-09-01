import os
from pathlib import Path

import config


class TestConfigDefaults:
    def test_app_name(self):
        assert config.APP_NAME == "Comar Chatbot"

    def test_app_version(self):
        assert config.APP_VERSION == "2.9.0"

    def test_host_default(self):
        assert config.HOST == "0.0.0.0"

    def test_port_default(self):
        assert config.PORT == 8000

    def test_model_name_default(self):
        assert config.MODEL_NAME == "qwen2.5:3b"

    def test_embedding_model_name(self):
        assert config.EMBEDDING_MODEL_NAME == "intfloat/multilingual-e5-small"

    def test_reranker_model_name(self):
        assert config.RERANKER_MODEL_NAME == "BAAI/bge-reranker-base"

    def test_chunk_size_default(self):
        assert config.CHUNK_SIZE == 1000

    def test_chunk_overlap_default(self):
        assert config.CHUNK_OVERLAP == 200

    def test_vector_search_top_k_default(self):
        assert config.VECTOR_SEARCH_TOP_K == 10

    def test_reranker_top_k_default(self):
        assert config.RERANKER_TOP_K == 5

    def test_memory_size_default(self):
        assert config.MEMORY_SIZE == 5

    def test_max_sessions_default(self):
        assert config.MAX_SESSIONS == 1000

    def test_cors_origins_default(self):
        assert config.CORS_ORIGINS == "http://localhost:8501"

    def test_log_level_default(self):
        assert config.LOG_LEVEL == "INFO"

    def test_log_format_default(self):
        assert config.LOG_FORMAT == "text"

    def test_base_dir_is_absolute(self):
        assert os.path.isabs(config.BASE_DIR)

    def test_directories_created(self):
        assert os.path.isdir(config.PDF_DIR)
        assert os.path.isdir(config.DOCX_DIR)
        assert os.path.isdir(config.TXT_DIR)
        assert os.path.isdir(config.CSV_DIR)
        assert os.path.isdir(config.CHROMA_DIR)
        assert os.path.isdir(config.CACHE_DIR)

    def test_chroma_collection_default(self):
        assert config.CHROMA_COLLECTION == "documents"
