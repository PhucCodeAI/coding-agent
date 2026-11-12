"""Exception handler toàn cục — mọi lỗi REST trả cùng một định dạng.

Response lỗi luôn có dạng:
    {"detail": ErrorEventPayload}   # code, title, message, hint, retryable, detail, context

Cùng payload với SSE event `error` → UI chỉ cần 1 cách hiển thị lỗi.
HTTP status lấy từ catalog (exceptions/catalog.py) theo mã lỗi.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.dtos.events import ErrorEventPayload
from app.errors import AppError, error_payload, error_payload_from_exc, get_error_spec

_STATUS_TO_CODE: dict[int, str] = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    422: "VALIDATION_ERROR",
}

_FIELD_TO_CODE: dict[str, str] = {
    "prompt": "EMPTY_PROMPT",
    "session_id": "INVALID_SESSION_ID",
}


def _response(payload: ErrorEventPayload, status_code: int | None = None) -> JSONResponse:
    """Đóng gói payload lỗi thành JSONResponse.

    Args:
        payload: Payload lỗi chuẩn.
        status_code: HTTP status. None = lấy từ catalog.

    Returns:
        JSONResponse `{"detail": payload}`.
    """
    status = status_code or get_error_spec(payload.code).http_status or 500
    return JSONResponse(
        status_code=status,
        content={"detail": payload.model_dump(exclude_none=True)},
    )


async def _handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    """AppError → status + thông báo theo catalog."""
    assert isinstance(exc, AppError)
    logger.warning("AppError {} — {}", exc.code, exc.message)
    return _response(error_payload_from_exc(exc))


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    """Lỗi validate request body/query → mã lỗi theo field đầu tiên bị lỗi."""
    assert isinstance(exc, RequestValidationError)
    errors: list[dict[str, Any]] = [
        {
            "field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"),
            "message": err.get("msg", ""),
        }
        for err in exc.errors()
    ]
    code = "VALIDATION_ERROR"
    for err in exc.errors():
        for part in err.get("loc", ()):
            if part in _FIELD_TO_CODE:
                code = _FIELD_TO_CODE[part]
                break
        if code != "VALIDATION_ERROR":
            break
    detail = "; ".join(f"{e['field']}: {e['message']}" for e in errors)
    return _response(error_payload(code, detail=detail, context={"errors": errors}), 422)


async def _handle_http_error(_: Request, exc: Exception) -> JSONResponse:
    """HTTPException (404 route, 405, ...) → mã lỗi chuẩn."""
    assert isinstance(exc, StarletteHTTPException)
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    code = _STATUS_TO_CODE.get(exc.status_code, "SYSTEM_ERROR")
    return _response(error_payload(code, detail=str(exc.detail)), exc.status_code)


class _CatchUnexpectedMiddleware:
    """ASGI middleware bắt Exception không lường trước → SYSTEM_ERROR 500.

    Không dùng add_exception_handler(Exception): Starlette chạy handler đó ở
    ServerErrorMiddleware (ngoài cùng) nên response thiếu header CORS và
    trình duyệt chỉ thấy "CORS error" thay vì thông báo lỗi.
    Viết dạng ASGI thuần (không BaseHTTPMiddleware) để không bọc
    StreamingResponse — giữ nguyên cơ chế cancel SSE khi client ngắt.
    """

    def __init__(self, app: Any) -> None:
        """Khởi tạo middleware.

        Args:
            app: ASGI app bên trong.
        """
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """Chạy app, chuyển exception thành response lỗi chuẩn nếu chưa gửi header."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def _send(message: Any) -> None:
            """Ghi nhận response đã bắt đầu gửi (để không gửi trang lỗi chồng lên)."""
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:
            logger.exception("Lỗi không mong đợi khi xử lý request")
            if started:
                raise
            response = _response(error_payload_from_exc(exc), 500)
            await response(scope, receive, send)


def register_error_handlers(app: FastAPI) -> None:
    """Đăng ký toàn bộ exception handler vào app.

    Phải gọi TRƯỚC `app.add_middleware(CORSMiddleware, ...)` để middleware
    bắt lỗi nằm bên trong CORS.

    Args:
        app: FastAPI app.
    """
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_middleware(_CatchUnexpectedMiddleware)


__all__ = ["register_error_handlers"]
