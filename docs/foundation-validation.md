# Kiểm chứng nền tảng — 14/11/2026

Đối chiếu 03.3 Confluence v7 và 03.6 v2 trước khi dựng. develop nền:
`020146f451600c2f0ccc9a085c97c97053bf6f92`. Issue #5.

Môi trường chạy: Linux, Python 3.12.14, uv 0.13.6, Node 24.22.1, npm 11.9.0.
Phiên bản thư viện chính xác nằm trong uv.lock/package-lock.json.

| Kiểm tra đã chạy | Kết quả |
| --- | --- |
| `uv sync --locked` | Cài thành công từ lockfile |
| `uv run ruff check .` | Đạt |
| `uv run ruff format --check .` | Đạt |
| `uv run pytest` | 10 passed; có cảnh báo deprecation httpx từ Starlette TestClient |
| `uv run alembic heads` | Đạt; chưa có revision nên chưa tạo schema |
| `npm ci` | Cài thành công từ package-lock |
| `npm run typecheck` | Đạt |
| `npm run build` | Đạt; Vite build thành công |
| Smoke qua Uvicorn thật | Khởi động một process, `/health/live` 200; index và hai asset build 200; readiness thiếu SQL 503; API/asset thiếu 404 |

Test readiness success/failure dùng probe giả lập, **không chứng minh kết nối SQL**.
Môi trường hiện không có ODBC driver đăng ký (`pyodbc.drivers() == []`) hoặc SQL Server.
Không có database, migration nghiệp vụ hoặc dữ liệu đo thật bị ghi/thay đổi.

Còn cần trước khi nghiệm thu toàn bộ nền tảng:

- Thành viên thứ hai làm theo README trên máy nhóm; ghi OS/runtime/SQL instance/driver.
- Cài ODBC Driver 18, cấu hình database test và kiểm chứng kết nối SQL Server thật.
- Khi migration đầu tiên đã review: áp dụng, kiểm tra ràng buộc, đọc/ghi và phục hồi trên DB test.
- Khi dựng worker: kiểm chứng một thread, dừng/restart, việc lưu bền vững và health khi worker chết.
- Kiểm tra CI GitHub, review PR và đặt required checks phù hợp. CI không thay approval.
- Kiểm thử AC/NFR từng luồng và phần cứng theo kế hoạch riêng.
