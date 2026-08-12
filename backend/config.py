import os
from dotenv import load_dotenv

load_dotenv()

APP_NAME = "Vilavi Chatbot"
APP_VERSION = "2.9.0"
HOST = "0.0.0.0"
PORT = 8000

# --- model storage ----------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
HUGGINGFACE_CACHE_DIR = os.path.join(MODELS_DIR, "huggingface")
os.environ.setdefault("HF_HOME", HUGGINGFACE_CACHE_DIR)
for d in [MODELS_DIR, HUGGINGFACE_CACHE_DIR]:
    os.makedirs(d, exist_ok=True)

# --- LLM provider -----------------------------------------------------------
# LLM_BASE_URL may be either the OpenAI-compat URL (…:11434/v1) or the native
# Ollama URL (…:11434). models.py normalises it.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
# Default aligned with .env — no more silent 3B fallback that would OOM on 8 GB.
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5:1.5b")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# --- Ollama model options ---------------------------------------------------
# num_ctx is the TOTAL context window (input + output). Ollama's default is
# 2048, far too small once you add rules + schema + history + query. Every
# analytics/RAG prompt was being silently truncated at that limit.
# num_predict caps response length so it doesn't eat all of num_ctx.
NUM_CTX = int(os.getenv("NUM_CTX", 8192))
NUM_PREDICT = int(os.getenv("NUM_PREDICT", 768))

# --- CORS -------------------------------------------------------------------
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:8501")

# --- logging ----------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = os.getenv("LOG_FORMAT", "text")

# --- chunking ---------------------------------------------------------------
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 1000))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 200))

# --- retrieval --------------------------------------------------------------
# Dropped from 20 → 10 so the CPU cross-encoder reranker stays under ~3 s.
VECTOR_SEARCH_TOP_K = int(os.getenv("TOP_K", 10))
RERANKER_TOP_K = int(os.getenv("TOP_N", 5))
# Smaller batch keeps peak reranker RAM under control on 8 GB VMs.
RERANKER_BATCH_SIZE = int(os.getenv("RERANKER_BATCH_SIZE", 8))

# Character budget for retrieved context. 4000 chars ≈ 1000 tokens — leaves
# headroom for system + rules + history inside num_ctx=8192.
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", 4000))

# Minimum chars for a chunk to be indexed.
MIN_CHUNK_CHARS = int(os.getenv("MIN_CHUNK_CHARS", 20))

EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-small"
RERANKER_MODEL_NAME = "BAAI/bge-reranker-base"

# --- conversation memory ----------------------------------------------------
MEMORY_SIZE = int(os.getenv("MEMORY_SIZE", 5))

# --- paths ------------------------------------------------------------------
CSV_DIR = os.path.join(BASE_DIR, "data", "csv")
CHROMA_PATH = os.path.join(BASE_DIR, "chroma_db")
CHROMA_COLLECTION = "documents"

PDF_DIR = os.path.join(BASE_DIR, "data", "pdf")
DOCX_DIR = os.path.join(BASE_DIR, "data", "docx")
TXT_DIR = os.path.join(BASE_DIR, "data", "txt")
CHROMA_DIR = CHROMA_PATH

CACHE_DIR = os.path.join(BASE_DIR, "cache", "dataframes")

# --- sessions ---------------------------------------------------------------
MAX_SESSIONS = int(os.getenv("MAX_SESSIONS", 1000))

# --- chat concurrency -------------------------------------------------------
# Ollama serves ONE request per model on CPU. Cap concurrency and queue depth
# so a burst of clicks doesn't OOM the VM or freeze the event loop.
MAX_CONCURRENT_CHATS = int(os.getenv("MAX_CONCURRENT_CHATS", 1))
CHAT_QUEUE_MAX = int(os.getenv("CHAT_QUEUE_MAX", 4))

# --- admin ------------------------------------------------------------------
# /ingest is protected. When ADMIN_TOKEN is empty, the endpoint refuses every
# call (401). Set this in .env for the operator only.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# --- dev / prod -------------------------------------------------------------
# uvicorn reload is DEV-ONLY. It spawns a watcher + worker, doubling resident
# memory, which is fatal on an 8 GB VM.
DEV_RELOAD = os.getenv("DEV_RELOAD", "0") == "1"

# --- analytics fallback -----------------------------------------------------
FALLBACK_ROW_THRESHOLD = 0
FALLBACK_ENABLED = True

# --- ensure data directories exist on startup -------------------------------
os.makedirs(PDF_DIR, exist_ok=True)
os.makedirs(DOCX_DIR, exist_ok=True)
os.makedirs(TXT_DIR, exist_ok=True)
os.makedirs(CSV_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
