# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/), phiên bản theo [SemVer](https://semver.org/lang/vi/) (xem [nguyên tắc nâng phiên bản](docs/ROADMAP.md#nguyên-tắc-nâng-phiên-bản)). Mỗi phiên bản ghi thời điểm phát hành dạng `YYYY-MM-DD HH:MM` kèm múi giờ.

## [0.1.0] - 2026-11-12 12:32 (UTC+07:00)

Phiên bản đầu tiên.

### Thêm
- **Agent tự viết, không framework:** vòng lặp tool-calling, client LLM bằng `httpx`, rút gọn ngữ cảnh, dừng khi hết bước, hết giờ hoặc lỗi lặp lại.
- **LLM nhiều nhà cung cấp** (Groq GPT-OSS, Google Gemini, OpenRouter), gọi theo vòng tròn: nhà nào trả lỗi (HTTP khác 200, output hỏng) thì chuyển ngay sang nhà kế tiếp; không giới hạn thời gian trả lời.
- **Sandbox Docker** cho mọi lệnh: không mạng, giới hạn RAM, CPU, pids và thời gian, không chạy bằng root (`user = "auto"`: cùng uid:gid với backend, chạy được cả trên Linux); `install_packages` là lệnh duy nhất có mạng.
- **Chốt chặn chất lượng code:** code Python phải qua `ruff check` (E9, F63, F7, F82) mới được ghi, có lỗi thì trả lỗi kèm vị trí để model sửa; nội dung bị cắt cụt thì hạ ưu tiên nhà cung cấp; không cho ghi đè file chưa đọc; tự `ruff format` sau khi ghi.
- **System prompt:** quy tắc riêng của dự án (quy trình, chuẩn code, ngôn ngữ, xử lý lỗi) và tích hợp nguyên tắc của [Andrej Karpathy Skills](https://github.com/multica-ai/andrej-karpathy-skills).
- **Lệnh `/` và đính kèm `@file`**, agent hỏi lại người dùng bằng câu hỏi trắc nghiệm.
- **Giao diện React:**
  - chat stream trực tiếp, render Markdown (bảng, task list, khối code);
  - workspace gồm cây file (sửa và lưu file), lịch sử commit kèm diff, terminal sandbox;
  - Trace Explorer: bước LLM lồng công cụ và lệnh sandbox;
  - lượt đang chạy không mất khi chuyển phiên.
- **Lưu trữ trên Supabase:** phiên, tin nhắn kèm sự kiện, trace (tóm tắt + span).
- **Công cụ phát triển:** `test.py` (chạy case với LLM thật, có cache), `simulate.py` (mô phỏng một lượt offline hoặc online, ghi request, phản hồi và token từng bước ra JSON).
- **Triển khai:** Docker Compose (backend + UI qua Nginx); CI chạy ruff, pytest và build UI.
