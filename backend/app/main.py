# app/main.py — Insight Analytics Backend (PRD §6 아키텍처)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import chat, documents, feedback, tableau

app = FastAPI(title="Insight Analytics Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://localhost:5175",  # Vite dev (5173 점유 시 5175)
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tableau.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(feedback.router)


@app.get("/healthz")
def healthz():
    s = get_settings()
    return {
        "ok": True,
        "gateway_mode": s.tableau_gateway_mode,
        "use_mcp": s.use_mcp,
        "mcp_endpoint": s.mcp_endpoint if s.use_mcp else None,
        "tableau_server": bool(s.tableau_server_base),
        "dify": bool(s.dify_dataset_api_key),
        "llm": {"router": s.llm_router_model, "synthesis": s.llm_synthesis_model},
        "database": s.database_url.split("://")[0],
    }
