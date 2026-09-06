**English** | [한국어](README.ko.md)

# Insight Analytics — Tableau + LLM Analytics Demo

A demo that fuses **structured queries over a Tableau data source** with **document knowledge (RAG)** into a single conversational assistant, so it can answer questions a dashboard alone cannot.

- **Structured data**: Tableau **VizQL Data Service (VDS)**, with the official **Tableau MCP server** available as an alternative path
- **Document knowledge**: **Dify** (RAG) across multiple knowledge bases, with agentic-lite domain routing
- **Orchestration**: a mini graph in FastAPI with SSE streaming — routing → VDS query → document search → fused synthesis
- **LLM**: local **Ollama** (fast no-think routing + synthesis)
- **Frontend**: **React + Vite** — embedded dashboard, chat, and evidence cards

---

## Architecture

```
React (Vite :5175)
   │  embedded view + question
   ▼
FastAPI backend (:8000)  ── mini graph (app/routers/chat.py, SSE)
   ├─ L1 routing (data query / document query / hybrid query)
   ├─ Data: TableauGateway ── direct VDS  ⇄  MCP server  (AUTO = VDS first, MCP fallback)
   ├─ Knowledge: Dify RAG (L2 domain selection)
   └─ Synthesis: Ollama → answer + evidence
```

Core modules: `backend/app/routers/chat.py` (mini graph), `backend/app/services/tableau_gateway.py` (VDS + MCP gateway), `backend/app/knowledge.py` (knowledge routing), `backend/app/services/dify.py`, `backend/app/services/llm.py`, `backend/app/config.py`.

---

## Features

- **L1 routing** — classifies each question as data / document / hybrid before doing any work.
- **VDS query generation** — the LLM builds query JSON from data source metadata, which is validated before execution. Schema differences between VDS variants (date filters, `limit` placement, operator naming) are absorbed with fallbacks.
- **L2 knowledge routing** — the model picks 0..N knowledge bases from a fixed registry, then Dify is searched and the results merged.
- **Gateway modes** — `TABLEAU_GATEWAY_MODE=VDS_ONLY | MCP_ONLY | AUTO`. MCP `query-datasource` reaches the **same VizQL Data Service**, so it is not a speed win; its value is the wider tool surface and a transport fallback.
- **Evidence** — answers cite their sources, and the view used for the analysis can be rendered server-side as an image.
- **Document upload and indexing**, feedback (👍/👎), dashboard summaries, and suggested questions.

---

## Repository layout

```text
backend/            FastAPI backend
  app/
    routers/        chat.py · tableau.py · documents.py · feedback.py
    services/       tableau_gateway.py (VDS + MCP) · dify.py · llm.py
    knowledge.py    knowledge routing registry and selection
    config.py       Pydantic Settings (reads .env)
  alembic/          database migrations
frontend/           React + Vite (:5175)
knowledge/          sample knowledge base — US retail market notes used for RAG
scripts/
  backend_up.sh     starts the FastAPI backend (auto-reload for development)
  tableau_mcp_up.sh starts the official @tableau/mcp-server over Streamable HTTP
  dify_provision.sh initial Dify provisioning
```

---

## Getting started

### 0. Prerequisites
- **Ollama** running locally (`http://localhost:11434`) with a model pulled
- **Dify** (self-hosted) — initialize with `scripts/dify_provision.sh`
- A **Tableau Cloud/Server** site and a Personal Access Token; a Connected App if you want token-based embedding
- Node (frontend), Python 3 (backend)

### 1. Configuration

**Backend** — copy the example and fill in your own values:
```bash
cp backend/.env.example backend/.env
# TABLEAU_SERVER / TABLEAU_SITE / TABLEAU_PAT_NAME / TABLEAU_PAT_SECRET / TABLEAU_DATASOURCE_LUID
# DIFY_* , OLLAMA_BASE_URL , USE_MCP , TABLEAU_GATEWAY_MODE
```

**Frontend** — the Tableau Cloud address is deployment-specific, so it is injected rather than hardcoded:
```bash
cp frontend/.env.example frontend/.env
# VITE_TABLEAU_HOST=https://<your-pod>.online.tableau.com   ← also loads the Embedding API
# VITE_TABLEAU_SITE=<your-site-id>                          ← the /t/<site> part of your Tableau URL
```
Both `.env` files are gitignored. The dashboards listed in `frontend/src/components/DashboardList.tsx` point at the Tableau **Samples** project (Superstore); change them to your own workbooks and views.

### 2. Backend
```bash
cd backend
python -m venv .venv && .venv/bin/pip install -U fastapi 'uvicorn[standard]' httpx pydantic-settings sqlalchemy pyyaml pyjwt
.venv/bin/python -m alembic upgrade head        # create the local SQLite schema
cd .. && scripts/backend_up.sh                  # :8000 with auto-reload
# PORT=8001 scripts/backend_up.sh               # pick a port / RELOAD=0 disables reload
```

### 3. Frontend
```bash
cd frontend && npm install && npm run dev    # http://localhost:5175
```

### 4. (Optional) Tableau MCP server
The MCP path is off by default.
```bash
scripts/tableau_mcp_up.sh      # @tableau/mcp-server over HTTP (reads credentials from backend/.env)
# Then set USE_MCP=true and TABLEAU_GATEWAY_MODE=AUTO in backend/.env and restart the backend
```

> ⚠️ **Use a separate PAT for MCP** if you run the MCP server and the backend at the same time. Tableau Cloud limits concurrent sessions per PAT, so sharing one makes the two invalidate each other's sessions (`401001`). Set `MCP_PAT_NAME` / `MCP_PAT_SECRET` in `backend/.env`; the script prefers those and falls back to `TABLEAU_PAT_*`.

> The MCP server's HTTP transport requires **OAuth 2.1 (per-user)** by default, and the script leaves that default in place. If you only want to exercise the backend's PAT-based gateway path locally, opt out for that run:
> ```bash
> DANGEROUSLY_DISABLE_OAUTH=true scripts/tableau_mcp_up.sh
> ```
> Never do this for an MCP server reachable beyond localhost — it removes per-user identity and permission enforcement.

---

## Key APIs

| Method · Path | Description |
|---|---|
| `GET /healthz` | Health and effective settings (`gateway_mode`, `use_mcp`, `mcp_endpoint`, LLM) |
| `POST /api/chat` | Conversation over SSE: route → step → citations → vds → token → done |
| `POST /api/chat/suggest` · `POST /api/chat/summary` | Suggested questions and dashboard summary |
| `GET /api/chat/history/{channel}` | Conversation history for a channel |
| `GET /api/tableau/metadata` · `POST /api/tableau/query` | VDS metadata and queries |
| `GET /api/tableau/view-image` | Server-rendered PNG of a view, used as evidence |
| `GET /api/tableau/embed-token` | Connected App JWT for embedding |
| `GET /api/tableau/mcp/tools` · `POST /api/tableau/mcp/tools/call` | List and call MCP tools (`USE_MCP=true`) |
| `POST /api/documents` · `GET /api/documents` · `DELETE /api/documents/{id}` | Document upload, listing, deletion |
| `POST /api/feedback` · `GET /api/feedback/stats` | Answer feedback and aggregates |

The `X-Gateway: MCP \| VDS` response header tells you which path actually served the request. Under `AUTO`, a failed MCP call falls back to VDS automatically.

---

## Notes

- `QUANTITATIVE_DATE` filters are not documented directly by Tableau, so the gateway tries several `RANGE`-style shapes and keeps whichever succeeds.
- `options.limit` and a top-level `limit` mean different things across VDS variants; the payload builders place it correctly per mode.
- The fallback modes exist so that a VDS schema change degrades gracefully instead of breaking the whole query path.
