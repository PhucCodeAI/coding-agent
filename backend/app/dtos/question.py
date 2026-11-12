"""Câu hỏi trắc nghiệm agent gửi người dùng và câu trả lời."""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QuestionOption(BaseModel):
    """1 lựa chọn cho câu hỏi gửi người dùng."""

    model_config = ConfigDict(extra="ignore")

    label: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=400)
    recommended: bool = False


class Question(BaseModel):
    """Câu hỏi nhiều lựa chọn. UI LUÔN thêm lựa chọn cuối "Khác" kèm ô nhập text."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default="", description="Mã câu hỏi (tự sinh nếu trống)")
    question: str = Field(..., min_length=1, max_length=500)
    options: list[QuestionOption] = Field(..., min_length=2, max_length=5)
    other_placeholder: str = Field(default="Nhập câu trả lời khác...", max_length=120)

    @model_validator(mode="after")
    def _normalize(self) -> Question:
        """Chuẩn hóa id câu hỏi và đảm bảo có đúng 1 lựa chọn khuyến nghị."""
        self.id = re.sub(r"[^a-z0-9_]", "_", self.id.lower())[:40] or f"q_{uuid.uuid4().hex[:6]}"
        if not any(o.recommended for o in self.options):
            self.options[0].recommended = True
        return self

    @property
    def recommended_label(self) -> str:
        """Nhãn lựa chọn được khuyến nghị (dùng khi người dùng không trả lời)."""
        return next(o.label for o in self.options if o.recommended)


class Answer(BaseModel):
    """Câu trả lời của người dùng cho 1 câu hỏi: chọn 1 option, hoặc tự nhập (Khác)."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(..., min_length=1, max_length=40)
    choice: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _one_of(self) -> Answer:
        """Bắt buộc có lựa chọn hoặc câu trả lời tự nhập."""
        if not (self.choice or (self.text and self.text.strip())):
            raise ValueError("Cần chọn 1 lựa chọn hoặc nhập câu trả lời khác")
        return self

    def as_text(self) -> str:
        """Câu trả lời dạng text để đưa vào prompt."""
        return (self.text or "").strip() or (self.choice or "")


@dataclass
class PendingQuestion:
    """1 lần hỏi đang chờ."""

    session_id: str
    request_id: str
    questions: list[Question]
    future: asyncio.Future[list[Answer]] = field(repr=False)


__all__ = ["Answer", "PendingQuestion", "Question", "QuestionOption"]
