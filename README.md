# 🤖 Comar Chatbot

> A business intelligence chatbot combining Retrieval-Augmented Generation (RAG) with structured data analytics. Built with FastAPI, LangChain, and Streamlit.

![Version](https://img.shields.io/badge/version-2.8.0-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-3.8%2B-blue?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?style=flat-square&logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)
![Last Updated](https://img.shields.io/badge/updated-June%202026-lightgrey?style=flat-square)

---

## 📋 Table of Contents

- [Overview](#overview)
- [✨ Features](#-features)
- [🏗️ Architecture](#️-architecture)
- [🛠️ Tech Stack](#️-tech-stack)
- [🚀 Getting Started](#-getting-started)
- [🐳 Docker Deployment](#-docker-deployment)
- [🖥️ Bare-metal Deployment (systemd)](#️-bare-metal-deployment-systemd)
- [⚙️ Configuration](#️-configuration)
- [💬 Usage](#-usage)
- [📡 API Reference](#-api-reference)
- [📁 Project Structure](#-project-structure)
- [🆕 What's New](#-whats-new-in-v280)
- [🗺️ Roadmap](#️-roadmap)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)

---

## Overview

Comar Chatbot enables natural language querying over both unstructured documents and structured data. It automatically routes questions to the right engine — semantic document search, pandas-powered analytics, or a hybrid of both — and returns answers in French with inline source citations.

---

## ✨ Features

### 📄 Document Intelligence (RAG)

| Capability             | Details                                                    |
| ---------------------- | ---------------------------------------------------------- |
| Multi-format ingestion | PDF, DOCX, and TXT files                                   |
| Semantic search        | ChromaDB vector database with multilingual E5 embeddings   |
| Intelligent reranking  | BGE reranker for improved relevance scoring                |
| Contextual compression | Refines retrieved chunks for higher precision              |
| Query condensation     | Resolves pronouns and references from conversation history |
| Source citations       | Every factual claim backed by an inline citation           |
| Language               | All responses generated in French 🇫🇷                     |

### 📊 Analytics Engine

| Capability                   | Details                                                                                  |
| ---------------------------- | ---------------------------------------------------------------------------------------- |
| Structured data processing   | Ingests CSV and Excel files                                                              |
| LLM-generated Pandas code    | Translates natural language into data queries                                            |
| Schema-aware                 | Understands column names, data types, and relationships                                  |
| Auto error correction        | Retries failed queries with corrected code (up to 3 attempts)                            |
| Computes on the full dataset | Aggregations run over every row with Pandas — never from a sample preview                |
| Sandboxed execution          | LLM-generated code is AST-validated (no imports, dunders, eval/exec/open) before running |
| Robust CSV parsing           | Auto-detects delimiters (`;`, `,`, tab, `                                                |
| Detailed answers             | Grounded, bullet-point summaries per requested metric, plus a structured results table   |
| Data normalization           | Fast column cleaning, explicit-format date parsing, type conversion                      |
| Smart keyword search         | Intelligently searches across multiple text columns                                      |

### 🧠 Intelligent Routing

| Capability                | Details                                                                                 |
| ------------------------- | --------------------------------------------------------------------------------------- |
| Query classification      | Auto-routes to RAG, Analytics, or Hybrid mode                                           |
| Parallel hybrid execution | Analytics and RAG retrieval run simultaneously                                          |
| Semantic caching          | Caches similar queries at 98% similarity threshold                                      |
| Per-session memory        | Isolated conversation history per browser session (cookie-based)                        |
| Grounded responses        | Figures come only from computed/retrieved data — no fabricated or "hypothetical" values |

### 🎨 Frontend

| Capability       | Details                                          |
| ---------------- | ------------------------------------------------ |
| Modern UI        | Glassmorphism design with gradient backgrounds   |
| Real-time chat   | Message history with expandable source citations |
| Data tables      | Interactive display for analytics results        |
| Health indicator | Live backend connection status                   |
| Memory reset     | Clear conversation history with one click        |

---

## 🏗️ Architecture

How a user query flows through the system:

```
                         ┌─────────────────────────────────────┐
                         │         Streamlit Frontend           │
                         │  Chat UI · Source viewer · Tables    │
                         └──────────────────┬──────────────────┘
                                            │ POST /chat
                         ┌──────────────────▼──────────────────┐
                         │           FastAPI Backend            │
                         │                                      │
                         │   ┌────────────────────────────┐    │
                         │   │     Semantic Cache (98%)    │    │
                         │   └──────────────┬─────────────┘    │
                         │                  │ cache miss        │
                         │   ┌──────────────▼─────────────┐    │
                         │   │       Query Router          │    │
                         │   └───────┬──────────┬──────────┘    │
                         │           │          │               │
                         │    ┌──────▼──┐  ┌───▼──────┐        │
                         │    │   RAG   │  │Analytics │        │
                         │    │ Pipeline│  │ Engine   │        │
                         │    └──────┬──┘  └───┬──────┘        │
                         │           │          │               │
                         │    ┌──────▼──┐  ┌───▼──────┐        │
                         │    │ChromaDB │  │  Pandas  │        │
                         │    │Vectors  │  │  + CSV   │        │
                         │    └─────────┘  └──────────┘        │
                         │                                      │
                         │         OpenRouter LLM               │
                         │       (GPT-4o-mini default)          │
                         └──────────────────────────────────────┘
```

### Query routing logic

```
User query
    │
    ├─── Contains data keywords? ──► Analytics Engine
    │         (sales, count, total…)       │
    │                                      │
    ├─── Contains doc keywords?  ──► RAG Pipeline
    │         (policy, terms, explain…)    │
    │                                      │
    └─── Both?  ─────────────────► Hybrid (parallel)
                                           │
                                    Merge & respond
```

---

## 🛠️ Tech Stack

```
┌─────────────────────────────────────────────────────┐
│                     Frontend                        │
│          Streamlit · Requests · Pandas              │
├─────────────────────────────────────────────────────┤
│                      API Layer                      │
│                FastAPI (async REST)                 │
├──────────────────────┬──────────────────────────────┤
│     RAG Pipeline     │       Analytics Engine       │
│  LangChain           │  Pandas · NumPy              │
│  ChromaDB            │  LLM code generation         │
│  E5 Embeddings       │  Auto error correction       │
│  BGE Reranker        │                              │
├──────────────────────┴──────────────────────────────┤
│                     Models                          │
│  LLM: OpenRouter (GPT-4o-mini)                      │
│  Embeddings: intfloat/multilingual-e5-large         │
│  Reranker:   BAAI/bge-reranker-base                 │
└─────────────────────────────────────────────────────┘
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.8+
- `pip`
- An [OpenRouter](https://openrouter.ai) API key

### Step 1 — Clone the repository

```bash
git clone <repository-url>
cd chatbot2
```

### Step 2 — Set up the backend

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the `backend/` directory:

```env
# Local Ollama (default — no key needed)
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
MODEL_NAME=qwen2.5:3b

# Or OpenRouter:
# LLM_BASE_URL=https://openrouter.ai/api/v1
# LLM_API_KEY=sk-or-v1-your-key-here
# MODEL_NAME=openai/gpt-4o-mini

CHUNK_SIZE=1000
CHUNK_OVERLAP=200
TOP_K=20
TOP_N=5
```

Place your data files in the correct directories:

```
backend/data/
├── 📁 pdf/              ← PDF files
├── 📁 docx/             ← Word documents
├── 📁 txt/              ← Plain text files
└── 📁 csv/              ← CSV or Excel files for analytics
```

Run ingestion, then start the server:

```bash
python ingest.py
python app.py
# ✅ Backend running at http://0.0.0.0:8000
```

### Step 3 — Set up the frontend

```bash
pip install -r requirements.txt

export API_URL=http://127.0.0.1:8000   # Windows: set API_URL=...
streamlit run app.py
# ✅ Frontend running at http://localhost:8501
```

---

## 🐳 Docker Deployment

### Prerequisites

- [Docker](https://docs.docker.com/engine/install/) and [Docker Compose](https://docs.docker.com/compose/install/)
- An [OpenRouter](https://openrouter.ai) API key

### Quick start

```bash
# 1. Clone the repository
git clone <repository-url>
cd chatbot2

# 2. Create your environment file
cp backend/.env.example backend/.env
# Edit backend/.env (defaults work with local Ollama)

# 3. Place your data files
# backend/data/pdf/   — PDF files
# backend/data/docx/  — Word documents
# backend/data/txt/   — Plain text files
# backend/data/csv/   — CSV or Excel files

# 4. Build and start
docker compose up -d

# 5. Run ingestion (first time)
curl -X POST http://localhost:8000/ingest

# ✅ Backend at http://localhost:8000
# ✅ Frontend at http://localhost:8501
```

### Volumes

| Volume | Mount point | Purpose |
|--------|-------------|---------|
| `chroma_data` | `/app/backend/chroma_db` | Persists the vector database |
| `cache_data` | `/app/backend/cache` | Persists pickled dataframes |
| `huggingface_cache` | `/root/.cache/huggingface` | Avoids re-downloading ML models |
| `./backend/data` (bind) | `/app/backend/data` | Your document and CSV files |

### Useful commands

```bash
# View logs
docker compose logs -f backend
docker compose logs -f frontend

# Rebuild after dependency changes
docker compose build --no-cache

# Stop everything
docker compose down

# Stop and delete volumes (warning: destroys data)
docker compose down -v
```

---

## 🖥️ Bare-metal Deployment (systemd)

### Prerequisites

- Python 3.11+
- An [OpenRouter](https://openrouter.ai) API key
- (Recommended) A dedicated `vilavi` system user

### Setup

```bash
# Create the deployment directory
sudo mkdir -p /opt/vilavi
sudo chown vilavi:vilavi /opt/vilavi

# Clone the repository
git clone <repository-url> /opt/vilavi

# Backend
cd /opt/vilavi/backend
python3 -m venv venv
source venv/bin/activate
pip install -r ../requirements.txt
cp .env.example .env
# Edit .env if needed (defaults work with local Ollama)
python ingest.py           # run ingestion first

# Frontend
cd /opt/vilavi/frontend_streamlit
python3 -m venv venv
source venv/bin/activate
pip install -r ../requirements.txt
```

### Installing systemd services

```bash
sudo cp /opt/vilavi/contrib/vilavi-backend.service /etc/systemd/system/
sudo cp /opt/vilavi/contrib/vilavi-frontend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable vilavi-backend vilavi-frontend
sudo systemctl start vilavi-backend vilavi-frontend
```

### Reverse proxy (NGINX example)

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location /api/ {
        rewrite ^/api/(.*) /$1 break;
        proxy_pass http://127.0.0.1:8000;
    }
}
```

---

## ⚙️ Configuration

Key parameters in `backend/config.py`. All can be overridden via `.env`.

| Parameter             | Default | Description                                                |
| --------------------- | ------- | ---------------------------------------------------------- |
| `CHUNK_SIZE`          | `1000`  | Token size for document chunks                             |
| `CHUNK_OVERLAP`       | `200`   | Overlap between adjacent chunks                            |
| `VECTOR_SEARCH_TOP_K` | `20`    | Documents retrieved before reranking                       |
| `RERANKER_TOP_K`      | `5`     | Documents retained after reranking                         |
| `MEMORY_SIZE`         | `5`      | Conversation turns (user+assistant pairs) held per session |
| `MAX_CONTEXT_CHARS`   | `12000`  | Max characters of retrieved context sent to the LLM        |
| `MIN_CHUNK_CHARS`     | `20`     | Minimum characters for a chunk to be indexed               |
| `CORS_ORIGINS`        | `http://localhost:8501` | Comma-separated allowed CORS origins          |
| `LOG_LEVEL`           | `INFO`   | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)        |
| `LOG_FORMAT`          | `text`   | Log format: `text` (human-readable) or `json` (machine)    |
| `MAX_SESSIONS`        | `1000`   | Maximum concurrent conversation sessions (LRU eviction)    |

### How chunk parameters affect quality

```
 CHUNK_SIZE  ──────────────────────────────────────────────►

 Small (500)   │ Fast · cheap · may lose context
               │
 Medium (1000) │ ✅ Recommended default — balanced precision
               │
 Large (2000)  │ Rich context · slower · higher cost

 CHUNK_OVERLAP ──────────────────────────────────────────────►

 Low (50)    │ Faster ingestion · may miss boundary content
 High (300)  │ ✅ Better continuity · larger index size
```

---

## 💬 Usage

### Document search 📄

```
"What are the warranty terms?"
"Explain the return policy."
"What payment options are available?"
```

### Data analytics 📊

```
"Show total sales by region."
"List all customers who signed up in 2023."
"What is the average order value?"
```

### Hybrid queries 🔀

```
"What is the return policy, and how many returns were processed last month?"
"Show me the warranty terms alongside related claim statistics."
```

---

## 📡 API Reference

### Endpoints at a glance

| Method | Endpoint        | Description                           |
| ------ | --------------- | ------------------------------------- |
| `GET`  | `/health`       | System status and document count      |
| `POST` | `/chat`         | Send a message and receive a response |
| `POST` | `/ingest`       | Trigger document re-indexing          |
| `POST` | `/clear-memory` | Reset conversation history            |

### `GET /health`

```bash
curl http://localhost:8000/health
```

### `POST /chat`

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What are the warranty terms?"}'
```

### `POST /ingest`

```bash
curl -X POST http://localhost:8000/ingest
```

### `POST /clear-memory`

```bash
curl -X POST http://localhost:8000/clear-memory
```

---

## 📁 Project Structure

```
chatbot2/
├── 📦 backend/
│   ├── app.py              # FastAPI application entry point
│   ├── config.py           # Configuration and environment variables
│   ├── rag.py              # RAG pipeline implementation
│   ├── analytics.py        # Analytics engine
│   ├── router.py           # Query routing logic
│   ├── session.py          # Per-session conversation memory (cookie-based)
│   ├── ingest.py           # Document & structured-data ingestion script
│   ├── models.py           # ML model loading and utilities
│   ├── Dockerfile
│   ├── .env.example        # Environment variable template
│   ├── 📂 data/
│   │   ├── pdf/            # PDF documents
│   │   ├── txt/            # Plain text documents
│   │   ├── docx/           # Word documents
│   │   └── csv/            # Structured data for analytics
│   └── 🗄️ chroma_db/       # Persisted vector database
├── 🎨 frontend_streamlit/
│   ├── app.py              # Streamlit UI
│   └── Dockerfile
├── 📁 contrib/
│   ├── vilavi-backend.service   # systemd unit for backend
│   └── vilavi-frontend.service  # systemd unit for frontend
├── requirements.txt        # All dependencies (backend + frontend + dev)
├── docker-compose.yml      # Docker orchestration
├── .dockerignore
└── README.md
```

---

## 🆕 What's New in v2.8.1

### Reliability & correctness

- **Accurate analytics** — analytical questions now compute over the **entire dataset** with Pandas. The old behavior embedded a 5-row preview into the vector store, which let the model answer from a tiny sample and invent totals; structured summaries are now **schema-only**.
- **No fabricated figures** — RAG, Hybrid, and Analytics prompts are grounded: numbers come only from retrieved/computed data, never "hypothetical" estimates.
- **Detailed, grounded answers** — multi-part analytics questions return a bold title + bullet points per requested metric, alongside a structured results table.
- **Robust CSV ingestion** — delimiter auto-detection (`;`, `,`, tab, `|`) and European decimal-comma handling fix mis-parsed single-column files.

### Security & stability

- **Sandboxed code execution** — LLM-generated Pandas code is AST-validated (no imports, dunder access, or `eval`/`exec`/`open`) and run with a minimal builtins set.
- **Cookie-based sessions** — conversation memory is isolated per browser session via a `session_id` cookie (`session.py`).
- **No more reload loops / double model loading** — dev autoreload excludes data, cache, and DB artifacts so heavy models load once.

### Quality of life

- **Structured logging** — all modules now use Python's `logging` (levelled, timestamped) instead of `print()`.
- **Faster startup** — date columns parse with a detected explicit format instead of slow per-element inference (no more `dateutil` warnings).

> ⚠️ After upgrading, run `POST /ingest` (or `python ingest.py`) to rebuild structured summaries as schema-only — existing previews persist in Chroma until re-indexed.

---

## 🗺️ Roadmap

### 🟢 Short-term

- [x] Per-session conversation memory (cookie-based) ✅ *v2.8.0*
- [ ] User authentication (login / API keys)
- [ ] Execution timeout & row caps for the analytics sandbox
- [ ] Real-time document upload through the UI
- [ ] Charts and graphs for analytics results
- [ ] API rate limiting for production
- [x] Docker images and CI/CD pipeline ✅ *v2.9.0*

### 🟡 Medium-term

- [ ] Hybrid keyword + semantic search
- [ ] Document versioning
- [ ] Response streaming for better UX
- [ ] User feedback collection for response quality improvement
- [ ] Webhook integrations for external systems

### 🔵 Long-term

- [ ] Multi-modal support (images, audio, video)
- [ ] Advanced RAG techniques (graph RAG, hierarchical RAG)
- [ ] Domain-specific model fine-tuning
- [ ] Horizontal scaling for large deployments
- [ ] Plugin system for third-party extensions

---

## 🤝 Contributing

Contributions are welcome! Please open an issue first to discuss what you'd like to change, then submit a pull request.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a pull request

---

## 📄 License

This project is licensed under the MIT License. See `LICENSE` for details.

---

## 🆘 Support

For questions or bug reports, please [open an issue](../../issues) in the repository.

---

<div align="center">

Made with ❤️ · **Version 2.8.1** · Last updated 13 June 2026

</div>
