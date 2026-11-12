# Lộ trình

Mỗi phiên bản đều chạy được và phát hành được. Mốc "xong" chung: clone repo mới, điền `.env`, chạy `docker compose up --build`, và chạy được ca thử mô tả bên dưới.

| Phiên bản | Nội dung | Tiêu chí nghiệm thu |
|---|---|---|
| **v0.1.0** | CI (ruff, pytest, build giao diện); hoàn thiện một easy case qua `docker compose up`; ảnh minh họa giao diện | Hoàn thiện nội dung và không có lỗi |
| **v0.2.0** | Định danh người dùng (đăng nhập Supabase Auth), mỗi người chỉ thấy phiên của mình. Phiên và trace đã lưu trên Supabase từ v0.1 | Hai tài khoản không thấy phiên của nhau; khởi động lại server vẫn thấy phiên cũ |
| **v0.3.0** | Danh sách việc (todo) agent tự lập và cập nhật, image sandbox có sẵn thư viện thường dùng (fastapi, pydantic, requests) | Việc nhiều bước hiện tiến độ; `/api` chạy test không cần cài thêm |
| **v0.4.0** | Bộ đánh giá ba mức (dễ, vừa, khó) chạy bằng một lệnh, in bảng tỉ lệ pass và token mỗi ca | Bảng số liệu thật trong README |
| **v1.0.0** | Image `backend` và `ui` trên GHCR (compose kéo sẵn, không cần build), video giới thiệu | Người mới chạy được trong vài phút |
| **v1.1.0** | Bot Telegram: chat với agent, nhận kết quả và file qua Telegram | Giao việc và nhận kết quả hoàn toàn qua Telegram |
| **v2** | Nhiều agent: agent điều phối giao việc cho agent chuyên môn, chạy song song | |
| **v3** | Bộ nhớ dài hạn: Graph + Embedding (tối ưu cho cá nhân người dùng) | |

## Nguyên tắc nâng phiên bản
Phiên bản theo [SemVer](https://semver.org/lang/vi/) dạng `MAJOR.MINOR.PATCH`.

**Chọn số nào để tăng**

| Thay đổi | Trước v1.0.0 | Từ v1.0.0 |
|---|---|---|
| Sửa lỗi, tài liệu, chỉnh prompt không đổi giao diện công khai | PATCH | PATCH |
| Tính năng mới, hoàn thành một mốc trong bảng trên | MINOR | MINOR |
| Thay đổi phá vỡ giao diện công khai | MINOR, ghi rõ ở mục "Thay đổi phá vỡ" của `CHANGELOG.md` | MAJOR |

Giao diện công khai gồm: endpoint API và tên/trường của sự kiện SSE, khoá trong `backend/config/*.toml`, tên biến trong `backend/.env`, schema bảng Supabase (`backend/migrations/`), tên dịch vụ và cổng trong `docker-compose.yml`. Đổi bất kỳ thứ nào trong số này mà người dùng cũ phải sửa cấu hình là thay đổi phá vỡ.

**Điều kiện để gắn tag**
1. CI xanh: ruff, pytest (model giả), build giao diện.
2. Tiêu chí nghiệm thu của mốc đạt với **model thật**, có ghi model, số lượt gọi và token đã dùng. Bản PATCH chỉ cần các case trong `backend/test.py` còn chạy được.
3. `CHANGELOG.md` có mục cho phiên bản (Thêm / Sửa / Thay đổi phá vỡ).
4. Số phiên bản trong `backend/pyproject.toml` và `ui/package.json` bằng nhau và bằng tag; chỉ đổi trong commit phát hành.
5. README và `docs/` khớp với hành vi của phiên bản.

**Cách phát hành**
- Tag có chú thích `vX.Y.Z` trên `main`; đẩy tag sẽ tạo bản phát hành.
- Không sửa, xoá hay gắn lại tag đã đẩy. Phát hành nhầm thì ra bản PATCH mới.
- Mỗi pull request chạy ruff và pytest với model giả (không tốn token).
