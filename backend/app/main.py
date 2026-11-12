"""Điểm vào FastAPI: dựng client / repository / service một lần trong lifespan, gắn router, CORS, xử lý lỗi.

Chạy: `uv run python -m app.main` (cổng theo `[server]` trong config.toml).
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.config import configs, tool_configs
from app.dtos.health import HealthStatus
from app.integrations import sandbox
from app.integrations.llm import LLMClient
from app.integrations.llm_dev import wrap_dev_llm
from app.integrations.supabase import SupabaseClient
from app.repositories.database import Database
from app.repositories.storage import Storage
from app.routers.chat import chat_router
from app.routers.error_handlers import register_error_handlers
from app.routers.sessions import session_router
from app.routers.traces import trace_router
from app.services.chat_service import ChatService
from app.services.session_service import SessionService
from app.services.trace_service import TraceService
from app.utils.logger import setup_logging

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Dựng và dọn các đối tượng dùng chung.

    Args:
        app: Ứng dụng FastAPI.

    Yields:
        None trong suốt thời gian phục vụ.
    """
    llm = wrap_dev_llm(LLMClient())
    supabase = SupabaseClient(configs.secrets.supabase_url, configs.secrets.supabase_key)
    db = Database(supabase)
    workspace_base = os.getenv("WORKSPACE_BASE") or configs.workspace.base
    app.state.traces = TraceService(db, Storage(supabase, configs.storage.bucket))
    app.state.sessions = SessionService(db, app.state.traces, workspace_base)
    app.state.chat = ChatService(llm, app.state.traces, app.state.sessions, workspace_base)
    with contextlib.suppress(Exception):
        await sandbox.cleanup_orphans()
    logger.info(
        "Sẵn sàng | workspace={} | model={} | công cụ={}", workspace_base, " → ".join(f"{p.name}:{p.model}" for p in llm.providers), tool_configs.agent.tools
    )
    try:
        yield
    finally:
        for close in (llm.aclose, supabase.aclose, sandbox.cleanup_orphans):
            with contextlib.suppress(Exception):
                await close()


app = FastAPI(title="Coding Agent", version="0.1.0", lifespan=lifespan)
app.include_router(chat_router)
app.include_router(session_router)
app.include_router(trace_router)
register_error_handlers(app)
app.add_middleware(CORSMiddleware, allow_origins=configs.server.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/health", response_model=HealthStatus, tags=["Health"])
async def health() -> HealthStatus:
    """Backend còn sống."""
    return HealthStatus(status="ok")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=configs.server.host, port=configs.server.port, log_config=None)
