"""Dependency của router: lấy các service đã dựng sẵn trong `main.lifespan` (lưu ở `app.state`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.services.chat_service import ChatService
from app.services.session_service import SessionService
from app.services.trace_service import TraceService


def chat_service(request: Request) -> ChatService:
    """ChatService dùng chung."""
    return request.app.state.chat


def session_service(request: Request) -> SessionService:
    """SessionService dùng chung."""
    return request.app.state.sessions


def trace_service(request: Request) -> TraceService:
    """TraceService dùng chung."""
    return request.app.state.traces


ChatDep = Annotated[ChatService, Depends(chat_service)]
SessionsDep = Annotated[SessionService, Depends(session_service)]
TracesDep = Annotated[TraceService, Depends(trace_service)]

__all__ = ["ChatDep", "SessionsDep", "TracesDep"]
