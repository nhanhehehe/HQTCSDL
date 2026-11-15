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

## G0-T04 — kiểm chứng contract Backend, 15/11/2026

Issue #8; base develop `b0cb45a73ebff576a8d2637f07aca6423c653ccd` (PR #6 đã merge).
Đối chiếu 03.5.1 Confluence v13 và G0-T04 tại 03.6. Hướng dẫn dùng code:
[backend-contract.md](backend-contract.md).

- Linux / Python 3.12.14; cài đúng backend/uv.lock, không đổi dependencies.
- Ruff lint/format đạt; pytest: 78 passed, gồm 10 test scaffold và 68 case contract.
- Alembic heads chạy đạt, chưa có revision; không tạo schema nghiệp vụ.
- Test contract kiểm strict JSON/presence/null, BIGINT/Decimal không mất độ chính xác,
  rowversion canonical 8 byte, UTC/range timezone, lỗi web/no-store/request ID,
  SQL failure giả lập không lộ secret và biên response ingestion độc lập.
- Có cảnh báo deprecation httpx từ Starlette TestClient như lần dựng khung.
- SQL lỗi trong test T04 dùng giả lập; không chứng minh SQL transaction qua Python,
  UQ/FK/locking/snapshot/nghiệp vụ hoặc hiệu năng đã đạt. Không gọi SQL trên máy Nhân.
- Ingestion/auth routes trong tests chỉ là fixtures; không đưa API nghiệp vụ giả vào app.

G0-T01 trên máy Nhân: bằng chứng SQL Server 2022 Express thật, hai DB dev/test,
quyền app/migration, đọc/ghi/commit/rollback/SNAPSHOT và dọn bảng đã được Nhân
cung cấp và ghi ở [03.6 mục 9](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14647366).
Nhân chọn dùng chung hai login theo vai trò trên hai DB; không tạo login test riêng.
Phân biệt bằng chứng đó với môi trường Linux/CI không có SQL của lần dựng khung.

Chưa chạy: Alembic M1/M2, transaction Python trên SQL Server thật, auth/browser,
worker/hardware/NFR và máy thành viên thứ hai. Câu lower/upper number/string giữa
mục 2 và ví dụ mục 10 của 03.5.1 cần đối chiếu trước code DTO monitoring; T04 không
tự chốt điểm lệch đó. PR này chưa nghiệm thu toàn bộ nền tảng.
