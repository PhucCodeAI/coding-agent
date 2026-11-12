"""Hỏi người dùng giữa lượt chạy: agent mở câu hỏi rồi chờ; API `/chat/answers` gửi câu trả lời.

1. `open()` → sự kiện SSE `question` (SSE vẫn mở nhờ heartbeat).
2. `await wait()` tới khi `resolve()` được gọi, hoặc hết giờ → dùng lựa chọn khuyến nghị.
Client ngắt kết nối → CancelledError lan vào `wait()` → câu hỏi bị đóng.
"""

from __future__ import annotations

import asyncio
import uuid

from app.dtos.question import Answer, PendingQuestion, Question
from app.errors import AnswerInvalidError, QuestionNotFoundError


class QuestionBroker:
    """Quản lý các câu hỏi đang chờ người dùng trả lời (in-process)."""

    def __init__(self) -> None:
        """Khởi tạo broker rỗng."""
        self._pending: dict[str, PendingQuestion] = {}

    def open(self, session_id: str, questions: list[Question]) -> PendingQuestion:
        """Mở 1 request hỏi người dùng.

        Args:
            session_id: Phiên đang chạy.
            questions: Câu hỏi.

        Returns:
            PendingQuestion (request_id dùng cho SSE + API trả lời).
        """
        req = PendingQuestion(
            session_id=session_id,
            request_id=f"q_{uuid.uuid4().hex[:12]}",
            questions=questions,
            future=asyncio.get_running_loop().create_future(),
        )
        self._pending[req.request_id] = req
        return req

    async def wait(self, req: PendingQuestion, timeout_sec: float) -> tuple[list[Answer], bool]:
        """Chờ câu trả lời.

        Args:
            req: Request đã mở.
            timeout_sec: Thời gian chờ tối đa.

        Returns:
            (answers, timed_out) — hết hạn thì answers = lựa chọn khuyến nghị.
        """
        try:
            return await asyncio.wait_for(asyncio.shield(req.future), timeout=timeout_sec), False
        except TimeoutError:
            return recommended_answers(req.questions), True
        finally:
            self._pending.pop(req.request_id, None)

    def resolve(self, session_id: str, request_id: str, answers: list[Answer]) -> None:
        """Nhận câu trả lời từ UI.

        Raises:
            QuestionNotFoundError: Không có request đang chờ khớp.
            AnswerInvalidError: Câu trả lời không hợp lệ.
        """
        req = self._pending.get(request_id)
        if req is None or req.session_id != session_id or req.future.done():
            raise QuestionNotFoundError(
                "Không có câu hỏi đang chờ (đã trả lời, hết hạn hoặc sai mã)",
                context={"session_id": session_id, "request_id": request_id},
            )
        req.future.set_result(_validate(req.questions, answers))


def recommended_answers(questions: list[Question]) -> list[Answer]:
    """Chọn lựa chọn khuyến nghị cho mọi câu hỏi."""
    return [Answer(question_id=q.id, choice=q.recommended_label) for q in questions]


def _validate(questions: list[Question], answers: list[Answer]) -> list[Answer]:
    """Khớp câu trả lời với câu hỏi; câu hỏi chưa trả lời dùng lựa chọn khuyến nghị.

    Args:
        questions: Câu hỏi đã gửi.
        answers: Câu trả lời người dùng gửi.

    Returns:
        Mỗi câu hỏi đúng 1 câu trả lời.

    Raises:
        AnswerInvalidError: Câu trả lời trỏ tới câu hỏi / lựa chọn không có.
    """
    by_id = {a.question_id: a for a in answers}
    out: list[Answer] = []
    for q in questions:
        a = by_id.get(q.id)
        if a is None:
            raise AnswerInvalidError(f"Thiếu câu trả lời cho: {q.question}", context={"question_id": q.id})
        labels = {o.label for o in q.options}
        if not (a.text and a.text.strip()) and a.choice not in labels:
            raise AnswerInvalidError(
                f"Lựa chọn '{a.choice}' không có trong câu hỏi: {q.question}",
                context={"question_id": q.id, "options": sorted(labels)},
            )
        out.append(a)
    return out


__all__ = [
    "QuestionBroker",
    "AnswerInvalidError",
    "QuestionNotFoundError",
    "PendingQuestion",
    "recommended_answers",
]
