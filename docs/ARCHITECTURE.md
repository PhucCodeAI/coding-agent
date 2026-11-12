# Kiến trúc

Một agent duy nhất, tự viết (không dùng framework agent): vòng lặp tool-calling gọi LLM qua `httpx`, chạy code trong container Docker, stream sự kiện qua SSE, lưu phiên và trace trên Supabase.

```
UI (React) ──POST /chat/run──▶ ChatService (khóa phiên, lịch sử, trace) ──▶ Agent (workflow) ──▶ LLM (httpx, nhiều nhà cung cấp)
   ▲   ▲                                                                           └──────────▶ công cụ: file, sandbox Docker, git
   │   └── POST /chat/answers ◀── câu hỏi của agent (event `question`)
   └──────── SSE: session · step · tool · file · sandbox · commit · question · answer · final · error · done
```

## Thư mục `backend/app/`

| Thư mục | Chứa |
|---|---|
| `dtos/` | Mọi schema có cấu trúc: request + sự kiện SSE, phiên, trace, cấu hình, trạng thái agent, câu hỏi, thao tác file, công cụ, lệnh, lỗi |
| `integrations/` | Dịch vụ bên ngoài: `llm` (+ `llm_dev`), `sandbox` (Docker), `git`, `ruff`, `supabase` (HTTP client) |
| `repositories/` | Truy cập dữ liệu theo nền tảng, không theo nghiệp vụ: `database` (CRUD PostgREST), `storage` (đọc/ghi object JSON), `workspace` (file cục bộ, chặn thoát thư mục) |
| `services/` | Nghiệp vụ router gọi: `chat_service`, `session_service`, `trace_service` |
| `routers/` | API FastAPI: `chat`, `sessions`, `traces`; xử lý lỗi chung |
| `workflow/` | Pipeline agent: `agent.py` (vòng lặp), `prompt.py` + `prompts/` (prompt, lệnh), `commands.py` (`/lệnh`, `@file`), `questions.py` (hỏi người dùng), `tools/` |
| `utils/` | Hàm dùng chung: `sse`, `logger`, `log_summary`, `py_outline` |
| `config.py` · `errors.py` · `main.py` | Nạp cấu hình · lỗi + danh mục mã lỗi · điểm vào |

Phụ thuộc một chiều: `routers → services → workflow / repositories → integrations`; `dtos`, `utils`, `errors` dùng được ở mọi tầng.

## Một lượt chat (`services/chat_service.py`)

1. Khóa theo phiên (1 phiên = 1 workspace + 1 git repo). Phiên đang chạy → `error` `SESSION_BUSY`.
2. Tạo phiên nếu chưa có, lấy lịch sử, lưu prompt → event `session`.
3. Chạy `Agent`; mọi sự kiện được yield ra SSE và ghi lại.
4. Không thành công → event `error` (tiêu đề, thông báo, gợi ý từ danh mục lỗi).
5. Lưu trace + lịch sử trong task riêng (kể cả khi client ngắt) → event `done` kèm token và `trace_id`.

## Agent (`workflow/agent.py`)

Brief = yêu cầu + chỉ dẫn của `/lệnh` + nội dung file `@` đính kèm + lịch sử phiên + danh sách file. Sau đó: gọi LLM kèm schema công cụ → chạy từng tool call → thêm kết quả → lặp.

| Dừng khi | Mã |
|---|---|
| Model gọi `finish` | `done`, hoặc `AGENT_INCOMPLETE` |
| Hết `max_steps` lời gọi LLM | `AGENT_STEP_LIMIT` |
| `max_fail_streak` (3) lệnh công cụ lỗi liên tiếp | `AGENT_REPEATED_FAILURE` |
| Quá `max_wall_sec` | `AGENT_TIMEOUT` |
| LLM không dùng được (hết quota, sai key...) | mã `LLM_*` |

- Mọi nhà cung cấp đều lỗi tạm thời qua đủ các vòng: chờ 15/30 giây rồi thử lại.
- Output hỏng (quá dài, tool call sai JSON): nhắc làm nhỏ lại, tối đa 2 lần. Không gọi công cụ: nhắc 2 lần rồi dừng.
- Rút gọn ngữ cảnh (`prompt.compact`): kết quả công cụ cũ hơn `keep_full_results` lượt còn `old_result_chars` ký tự (kết quả đọc file giữ 3000); tổng request vượt `request_max_chars` thì rút tiếp từ cũ nhất. Lời gọi công cụ của chính agent không bị sửa.
- Thành công và có file được ghi → tự commit.

## Lệnh `/` và đính kèm `@` (`workflow/commands.py`)

Người dùng gõ nguyên văn, VD `/test thêm case số âm cho @calc.py`. `/tên` ở đầu tin nhắn là lệnh hệ thống (danh sách trong `workflow/prompts/commands.toml`: test, fix, refactor, api, sql, perf, docs, explain, review; lệnh `chat` chỉ đọc). `@đường_dẫn` khớp file trong workspace được đính kèm nội dung (tối đa 4 file × 8000 ký tự) và coi như đã đọc. Tin nhắn lưu nguyên văn; UI có menu gợi ý cho cả hai (`GET /chat/commands`, `GET /sessions/{id}/files`).

## Công cụ (`workflow/tools/`)

Khai báo `@tool(group=...)` + docstring Google ở `definitions.py` (docstring là mô tả gửi LLM; thiếu thì lỗi lúc import), logic ở `handlers.py`, đăng ký + schema + ép kiểu ở `registry.py`. Danh mục: `uv run python -m app.workflow.tools`.

| Nhóm (`[agent] tools`) | Công cụ |
|---|---|
| `workspace_read` | `list_files`, `read_file`, `read_outline`, `search_text` |
| `workspace_write` | `write_file`, `edit_file`, `delete_file` (ghi đè file đã có phải đọc trước; `.py` được `ruff check` (E9, F63, F7, F82) **trước khi ghi**, có lỗi thì không ghi và trả lỗi kèm vị trí; nội dung bị cắt cụt thì hạ ưu tiên nhà cung cấp; ghi xong tự ruff format) |
| `test` · `shell` | `run_tests` (pytest), `run_command` (python) trong sandbox |
| `install` | `install_packages` (pip; chỉ lệnh này có mạng) |
| `inspect` | `read_log`, `find_files`, `check_syntax`, `pip_list`, `git_log`, `system_info` |
| `git_read` · `git_write` · `format` | `git_status`, `git_diff` · `git_commit` · `format_files` |
| luôn có | `finish`; `ask_user` khi `ask_human = true` |

Mỗi công cụ thêm khoảng 300 token vào mọi lời gọi. Thêm công cụ: hàm ở `definitions.py` + logic ở `handlers.py` + nhóm trong `[agent] tools` + icon ở `ui/src/components/chat/ToolRow.tsx`.

## LLM (`integrations/llm.py`)

- Nhà cung cấp tương thích OpenAI chat/completions, gọi bằng `httpx`; payload, parse tool call, chuẩn hóa token đều tự viết.
- `[[llm.providers]]`: thứ tự = ưu tiên, thiếu key thì bỏ qua. Gọi theo **vòng tròn**: nhà nào trả lỗi (HTTP ≠ 200 như 429, 5xx; hoặc output hỏng) thì chuyển **ngay** sang nhà kế tiếp, không chờ hay thử lại cùng nhà; hết vòng quay lại từ đầu, tối đa `failover_rounds` vòng. Ở vòng sau, nhà vừa bị 429 còn hồi ≤ `rate_limit_max_wait_sec` thì chờ nốt (thời gian hồi đọc từ header `Retry-After` hoặc "try again in …" trong body). Nhà lỗi hạ tầng xếp cuối trong `provider_cooldown_sec`; hết hạn mức ngày bị bỏ qua tới lúc reset. Không giới hạn thời gian trả lời: model chậm vẫn được chờ, chỉ lỗi thật mới chuyển nhà. Chỉ có 1 nhà thì thử `attempts_per_provider` lần.
- Gemini 3: giữ `extra_content` (thought_signature) của tool call.
- Chế độ dev (`[llm] dev_mode` / env `LLM_DEV_MODE`): `cache` phát lại câu trả lời đã ghi theo nội dung hội thoại (`storage/lab/cache`); `demo` là kịch bản cố định 0 token để thử giao diện.

## Sandbox (`integrations/sandbox.py`)

Mỗi lệnh 1 container `python:3.12-slim`: `--network none`, giới hạn RAM/CPU/pids, mount workspace. User (`[tools.sandbox] user = "auto"`): cùng uid:gid với backend; backend chạy root (trong Docker) thì dùng 1000:1000 và chuyển chủ workspace cho user này — trên Linux lệch uid là pip/pytest không ghi được vào workspace. Lần chạy test đầu tự cài pytest vào `.deps` của workspace (lệnh pip có mạng). Backend chạy trong container thì đường dẫn mount đổi sang đường dẫn host (`HOST_WORKSPACE_DIR`, tự dò bằng `docker inspect`). Test lỗi chỉ gửi bản tóm tắt, log đầy đủ lấy bằng `read_log`.

## Sự kiện SSE (`dtos/events.py`)

| Event | Dữ liệu |
|---|---|
| `session` | `session_id`, `title` — luôn đầu tiên |
| `step` | 1 lời gọi LLM: `step`, `model`, `provider`, `input_tokens`, `output_tokens`, `duration_ms`, `content`, `tool_calls[{id, name, args}]` |
| `tool` | kết quả 1 công cụ: `id` (khớp tool call), `name`, `ok`, `output`, `duration_ms` |
| `file` · `sandbox` · `commit` | file đã ghi/xóa · lệnh sandbox · commit |
| `question` · `answer` | agent hỏi (trắc nghiệm + tự nhập) · câu trả lời / hết giờ |
| `final` | `status` (done/failed), `text` (Markdown), `code` |
| `error` | `code`, `title`, `message`, `hint` |
| `done` | trạng thái cuối, token, thời gian, `trace_id` — luôn cuối cùng |

Mỗi lượt lưu nguyên chuỗi sự kiện; UI dựng giao diện từ chuỗi đó nên mở lại phiên cũ thấy y như lúc chạy.

## API

| Endpoint | |
|---|---|
| `POST /chat/run` | `{prompt, session_id?}` → SSE (heartbeat `: ping`); client ngắt = hủy lượt |
| `POST /chat/answers` | trả lời câu hỏi đang chờ |
| `GET /chat/commands` | danh sách lệnh `/` |
| `GET /sessions` · `GET/PATCH/DELETE /sessions/{id}` | danh sách · chi tiết (kèm sự kiện từng lượt) · đổi tên `{title}` · xóa (tin nhắn, trace, workspace) |
| `GET /sessions/{id}/files[/content?path=]` · `PUT /sessions/{id}/files/content` | đọc workspace · ghi file `{path, content}` (sửa từ UI, không tự commit) |
| `POST /sessions/{id}/exec` | `{command}` → chạy `sh -c` trong sandbox của phiên (không mạng), trả `{exit_code, stdout, stderr, duration_ms, is_timeout}` |
| `GET /sessions/{id}/commits` · `GET /sessions/{id}/commits/{hash}/diff` · `GET /sessions/{id}/changes` | lịch sử commit · diff 1 commit · thay đổi chưa commit |
| `GET /traces/sessions?q=&status=&limit=&offset=` · `GET/DELETE /traces/sessions/{id}` | trang phiên có trace `{items, total}` · chi tiết kèm lượt · xóa trace của phiên |
| `GET /traces/sessions/{id}/live` · `GET /traces/{trace_id}` | trace lượt đang chạy · trace 1 lượt (span) |
| `GET /health` | backend còn sống |

Ghi file, chạy lệnh và xóa phiên bị từ chối (`SESSION_BUSY`, 409) khi agent đang chạy trong phiên.

## Phiên và trace (Supabase)

- Bảng `sessions`, `session_messages` (tin nhắn kèm sự kiện của lượt), `traces` (tóm tắt lượt) + bucket `trace-logs` (span đầy đủ ở `<session_id>/<trace_id>.json`). Ghi blob trước rồi dòng tóm tắt; ghi dòng lỗi thì xóa blob.
- Span: `llm` (mỗi lời gọi, kèm token/model), `tool` (mỗi công cụ), `sandbox` (mỗi lệnh Docker).
- Khởi tạo: chạy `backend/migrations/001_init_supabase.sql` trong SQL Editor của Supabase.

## Lỗi (`errors.py`)

Cây exception (`AppError` mang mã lỗi) + danh mục mã lỗi (tiêu đề, thông báo, cách xử lý, HTTP status, có thử lại được không) + cách dựng event `error`. Mã chưa khai báo rơi về nhóm theo tiền tố (`LLM_`, `SANDBOX_`, `AGENT_`...).

## Cấu hình

- `backend/.env`: `GROQ_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY` (ít nhất 1), `SUPABASE_URL`, `SUPABASE_KEY`; tùy chọn `LLM_DEV_MODE`.
- `backend/config/config.toml`: log, server, tên bảng/bucket, thư mục workspace.
- `backend/config/tool_config.toml`: `[llm]`, `[tools.*]` (sandbox, git, workspace, formatter), `[agent]` (nhóm công cụ, giới hạn, rút gọn ngữ cảnh, hỏi người dùng, commit). Sai cấu hình → backend dừng lúc khởi động.
- Prompt: `backend/app/workflow/prompts/` (`agent.md`, `commands.toml`) — không đặt prompt trong code hay config.

## Kiểm thử

- `uv run pytest` (trong `backend/`, LLM giả, 0 token): ghi file → finish → commit; chặn ghi đè chưa đọc; chặn thoát workspace; dừng sau 3 lỗi; hết số bước; `/lệnh` + `@file` vào brief; pytest thật trong Docker (bỏ qua nếu không có Docker); mọi hàm/class có docstring.
- `uv run python test.py run <case>`: chạy case với LLM thật có cache (`--mode replay` 0 token, `--fake-sandbox` không cần Docker); `turns <case>` liệt kê từng lượt và token.

## Quyết định

1. **Tự viết, không framework agent**: kiểm soát toàn bộ payload, retry, rút gọn ngữ cảnh, sự kiện; ít phụ thuộc. Đánh đổi: tự bảo trì client LLM và vòng lặp.
2. **Một agent**: đơn giản, dễ đo; nhiều agent để sau.
3. **Sandbox Docker bắt buộc**, không mạng khi chạy code; chỉ lệnh cài thư viện có mạng.
4. **Supabase** cho phiên và trace: mở cùng phiên ở thiết bị khác, không mất khi khởi động lại.
5. **UI dựng từ chuỗi sự kiện**: một đường xử lý cho cả lượt đang chạy và phiên cũ.
6. **Repository theo nền tảng** (database, storage, file) thay vì theo nghiệp vụ; truy vấn cụ thể nằm ở service.
