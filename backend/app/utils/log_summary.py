"""Tóm tắt log chạy test / lệnh: chỉ giữ phần cho biết SAI Ở ĐÂU (không dán nguyên log dài)."""

from __future__ import annotations

import re

_COUNTS = re.compile(
    r"^\d+ (?:passed|failed|error|errors|skipped)\b.* in [\d.]+s.*$|^=+ .*\b(?:passed|failed|error|errors|no tests ran)\b.* =+$|^Ran \d+ tests?.*$|^(?:OK|FAILED)\b.*$",
    re.M,
)
_SHORT = re.compile(r"^(?:FAILED|ERROR) \S+.*$", re.M)
_ERR_LINE = re.compile(r"^E\s+(.+)$", re.M)
_LOCATION = re.compile(r"^([\w./-]+\.py):(\d+): ?(.*)$", re.M)
_FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+), in (\S+)', re.M)
_EXC = re.compile(r"^(?:[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)|AssertionError)\b.*$", re.M)
_ENV = re.compile(r"(site-packages|/usr/lib/python|/usr/local/lib/python|<frozen )")


def summarize_log(log: str, max_chars: int = 1500) -> str:
    """Rút log dài thành: dòng tổng kết, test hỏng, nơi sai (file:dòng), lỗi gốc. Không tìm thấy gì → đuôi log.

    Args:
        log: toàn bộ stdout+stderr.
        max_chars: giới hạn độ dài kết quả.

    Returns:
        Văn bản ngắn, mỗi ý 1 dòng.
    """
    log = log.replace("\r", "")
    parts: list[str] = []

    counts = [m.group(0).strip("= ").strip() for m in _COUNTS.finditer(log)]
    if counts:
        parts.append("Tổng kết: " + counts[-1])

    failed = list(dict.fromkeys(m.group(0).strip() for m in _SHORT.finditer(log)))
    if failed:
        parts.append("Hỏng:\n" + "\n".join(f"  - {x[:200]}" for x in failed[:8]) + (f"\n  … +{len(failed) - 8}" if len(failed) > 8 else ""))

    where: list[str] = []
    for m in _LOCATION.finditer(log):
        where.append(f"{m.group(1)}:{m.group(2)}" + (f" {m.group(3).strip()[:120]}" if m.group(3).strip() else ""))
    if not where:
        frames = [(f, ln, fn) for f, ln, fn in _FRAME.findall(log) if not _ENV.search(f)]
        where = [f"{f.replace('/workspace/', '')}:{ln} trong {fn}" for f, ln, fn in frames[-4:]]
    if where:
        parts.append("Nơi sai (file:dòng):\n" + "\n".join(f"  - {w}" for w in list(dict.fromkeys(where))[-6:]))

    errs = list(dict.fromkeys(m.group(1).strip() for m in _ERR_LINE.finditer(log)))
    if not errs:
        errs = list(dict.fromkeys(m.group(0).strip() for m in _EXC.finditer(log)))
    if errs:
        parts.append("Lỗi gốc:\n" + "\n".join(f"  - {e[:220]}" for e in errs[:6]))

    if not parts:
        tail = log.strip()[-max_chars:]
        return "(không nhận ra cấu trúc lỗi) …" + tail if tail else "(log rỗng)"
    return "\n".join(parts)[:max_chars]
