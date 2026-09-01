import os
import sys
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# PyInstaller frozen: .env à côté de l'exe (HAYETT_Admin_Tools/.env)
if getattr(sys, 'frozen', False):
    _exe_dir = os.path.dirname(sys.executable)
    _env_path = os.path.join(_exe_dir, ".env")
else:
    _env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(_env_path)

APP_NAME = "Comar Chatbot"
APP_VERSION = "2.9.0"
HOST = "0.0.0.0"
PORT = 8000

# --- model storage ----------------------------------------------------------
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
# Character budget for the rendered history string (session.py). A turn cap
# alone is not enough when a single message is huge (long pasted text).
HISTORY_MAX_CHARS = int(os.getenv("HISTORY_MAX_CHARS", 2000))

# --- semantic cache ----------------------------------------------------------
# Similarity threshold for the secondary embedding match in models.SemanticCache.
# Kept high ON PURPOSE: a near-miss query ("garantie produit A" vs "produit B")
# can embed very closely, and a cached wrong answer is a hallucination risk.
# Clamped to [0.80, 1.0]; the exact-key primary path is unaffected by this value.
SEMANTIC_CACHE_THRESHOLD = min(1.0, max(0.80, float(os.getenv("SEMANTIC_CACHE_THRESHOLD", 0.97))))

# --- paths ------------------------------------------------------------------
CSV_DIR = os.path.join(BASE_DIR, "data", "csv")
CHROMA_PATH = os.path.join(BASE_DIR, "chroma_db")
CHROMA_COLLECTION = "documents"

PDF_DIR = os.path.join(BASE_DIR, "data", "pdf")
DOCX_DIR = os.path.join(BASE_DIR, "data", "docx")
TXT_DIR = os.path.join(BASE_DIR, "data", "txt")
MD_DIR = os.path.join(BASE_DIR, "data", "md")
CHROMA_DIR = CHROMA_PATH

CACHE_DIR = os.path.join(BASE_DIR, "cache", "dataframes")

# --- Oracle database --------------------------------------------------------
# Oracle is the source of client/contract data (COMPTE, CLIENT, CONTRAT,
# VERSEMENT, EPARGNE, BENEFICIAIRE). Fill these in .env; when any is empty the
# DB layer is unavailable and only RAG (documents) + pandas/CSV analytics run.
# ORACLE_DSN may be given directly (e.g. "localhost:1521/FREEPDB1") or built
# from ORACLE_HOST/ORACLE_PORT/ORACLE_SERVICE.
ORACLE_USER = os.getenv("ORACLE_USER", "")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD", "")
ORACLE_HOST = os.getenv("ORACLE_HOST", "localhost")
ORACLE_PORT = os.getenv("ORACLE_PORT", "1521")
ORACLE_SERVICE = os.getenv("ORACLE_SERVICE", "FREEPDB1")
ORACLE_DSN = os.getenv("ORACLE_DSN", "")
if not ORACLE_DSN:
    ORACLE_DSN = f"{ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE}"
# Seeding (schema + sample clients) is an EXPLICIT, opt-in operation (see
# database.py). It is OFF by default: the backend never creates or mutates the
# existing HAYETT_USER tables unless the operator sets this to 1.
SEED_DB_ON_START = os.getenv("SEED_DB_ON_START", "0") == "1"

# --- authentication ---------------------------------------------------------
# How long a login stays bound to a browser session (seconds).
AUTH_TTL_SECONDS = int(os.getenv("AUTH_TTL_SECONDS", 60 * 60 * 12))

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

# --- email (creation compte client) -----------------------------------------
# SMTP standard (stdlib smtplib). Aucune valeur en dur, tout via .env.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1") == "1"
SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", 10))

# --- analytics fallback -----------------------------------------------------
FALLBACK_ROW_THRESHOLD = 0
FALLBACK_ENABLED = True

# --- ensure data directories exist on startup -------------------------------
os.makedirs(PDF_DIR, exist_ok=True)
os.makedirs(DOCX_DIR, exist_ok=True)
os.makedirs(TXT_DIR, exist_ok=True)
os.makedirs(MD_DIR, exist_ok=True)
os.makedirs(CSV_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
