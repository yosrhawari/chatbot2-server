import logging

from config import LOG_LEVEL

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import (
    ADMIN_TOKEN,
    APP_NAME,
    APP_VERSION,
    CHAT_QUEUE_MAX,
    CORS_ORIGINS,
    DEV_RELOAD,
    HOST,
    MAX_CONCURRENT_CHATS,
    PORT,
)

from ingest import (
    ingest_all,
    get_collection_count,
)

from router import route_query
from session import session_manager
from analytics import load_dataframes
from models import semantic_cache
from auth import auth_store, login as auth_login, client_info
from database import init_db, db_available


SESSION_COOKIE = "session_id"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def _get_session_id(request: Request) -> tuple[str, bool]:
    """Return (session_id, is_new). Reuses the cookie if present, else mints one."""
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        return sid, False
    return uuid.uuid4().hex, True


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


# --- concurrency control ---------------------------------------------------
# Ollama serves ONE request at a time per model on CPU. Without a cap, N
# simultaneous /chat calls all block a worker each while queuing at Ollama,
# and the FastAPI event loop stops responding to /health. We:
#   1) run route_query in a thread (blocking CPU work),
#   2) limit concurrency with a semaphore,
#   3) return 503 immediately if the queue is already full.
_chat_semaphore = None
_in_flight = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _chat_semaphore
    _chat_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHATS)
    logger.info(
        f"[STARTUP] Chat concurrency={MAX_CONCURRENT_CHATS} queue_max={CHAT_QUEUE_MAX}"
    )
    logger.info("[STARTUP] Pre-loading and normalizing DataFrames...")
    load_dataframes()
    logger.info("[STARTUP] Initializing Oracle schema...")
    init_db()
    yield
    logger.info("[SHUTDOWN] Stopping application...")


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    lifespan=lifespan,
)


# CORS. allow_credentials=True is required for the browser to include the
# session cookie on cross-origin requests (Streamlit at :8501 → API at :8000).
_cors_origins = [o.strip() for o in CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str


class LoginRequest(BaseModel):
    email: str
    password: str


@app.get("/health")
def health():
    return {
        "status": "ok",
        "vector_documents": get_collection_count(),
        "in_flight_chats": _in_flight,
        "database": "oracle" if db_available() else "unavailable",
    }


# ── Authentication (plan §7) ─────────────────────────────────────────────────
# Login verifies COMPTE.password_hash (bcrypt), then binds the client_id to
# the browser session cookie SERVER-side. /chat only ever uses this binding:
# the client_id is never taken from the question text.


@app.post("/login")
def login(
    body: LoginRequest,
    http_request: Request,
    response: Response,
):
    if not db_available():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable. Check ORACLE_* settings in the server .env.",
        )
    result = auth_login(body.email, body.password)
    if result is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    session_id, _ = _get_session_id(http_request)
    auth_store.bind(session_id, result["client_id"])
    _set_session_cookie(response, session_id)
    return {
        "status": "success",
        "client_id": result["client_id"],
        "nom": result["nom"],
        "prenom": result["prenom"],
        "email": result["email"],
    }


@app.post("/logout")
def logout(http_request: Request, response: Response):
    session_id = http_request.cookies.get(SESSION_COOKIE)
    if session_id:
        auth_store.unbind(session_id)
        response.delete_cookie(SESSION_COOKIE)
    return {"status": "logged out"}


@app.get("/me")
def me(http_request: Request):
    session_id = http_request.cookies.get(SESSION_COOKIE)
    client_id = auth_store.get_client_id(session_id) if session_id else None
    if client_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    info = client_info(client_id)
    if info is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return {"status": "authenticated", **info}


def _check_admin(token: str) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Admin operations disabled. Set ADMIN_TOKEN in the server .env.",
        )
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.post("/ingest")
def ingest(x_admin_token: str = Header(default="")):
    _check_admin(x_admin_token)
    ingest_all()
    load_dataframes()
    semantic_cache.clear()
    logger.info("[INGEST] Flushed semantic cache after re-ingestion.")
    return {
        "status": "success",
        "vector_documents": get_collection_count(),
    }


@app.post("/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    response: Response,
):
    global _in_flight

    # Fast rejection when the queue is already full. Single-threaded event
    # loop — check+increment below is atomic because there's no await between.
    if _in_flight >= CHAT_QUEUE_MAX:
        return JSONResponse(
            status_code=503,
            content={
                "error": "server busy",
                "detail": "Too many concurrent chat requests. Please retry shortly.",
            },
        )
    _in_flight += 1

    try:
        assert _chat_semaphore is not None
        async with _chat_semaphore:
            session_id, _ = _get_session_id(http_request)
            # Resolve the authenticated client from the server-side store.
            client_id = auth_store.get_client_id(session_id)
            logger.debug(
                "[CHAT] session_id=%s client_id=%s message=%r",
                session_id,
                client_id,
                (request.message or "")[:120],
            )
            # route_query is blocking (LLM + reranker on CPU). Off-load to a
            # worker thread so the event loop stays responsive to /health etc.
            result = await asyncio.to_thread(
                route_query, request.message, session_id, client_id
            )
            _set_session_cookie(response, session_id)
            return result
    except Exception:
        logger.exception("Unhandled error in /chat")
        return JSONResponse(
            status_code=500,
            content={"error": "internal error"},
        )
    finally:
        _in_flight -= 1


@app.post("/clear-memory")
def clear_memory(http_request: Request):
    sid = http_request.cookies.get(SESSION_COOKIE)
    if not sid:
        # No cookie → nothing to clear. Do NOT mint a new session just to
        # clear it (that lets hostile callers fill the SessionManager LRU).
        raise HTTPException(status_code=400, detail="no active session")
    session_manager.get_memory(sid).clear()
    return {"status": "memory cleared"}


@app.get("/")
def root():
    return {
        "application": APP_NAME,
        "version": APP_VERSION,
        "status": "yekhdem mrigel",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=HOST,
        port=PORT,
        reload=DEV_RELOAD,
        reload_includes=["*.py"] if DEV_RELOAD else None,
        reload_excludes=[
            "chroma_db/*", "chroma_db/**",
            "cache/*", "cache/**",
            "data/*", "data/**",
            "__pycache__/*", "**/__pycache__/**",
            "*.pkl", "*.sqlite3", "*.sqlite3-journal", "*.bin", "*.log",
        ] if DEV_RELOAD else None,
    )
