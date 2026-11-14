# Môi trường và hướng dẫn SQL Server

| Profile `HQ_ENV` | Database gợi ý | Dữ liệu |
| --- | --- | --- |
| `dev` | `HQTCSDL_dev_<ten>` | Local riêng từng thành viên |
| `test` | `HQTCSDL_test` | Fixture/simulator, tách khỏi đo thật |
| `demo` | `HQTCSDL_demo` | Dữ liệu demo/thiết bị thật |

`HQ_ENV` là nhãn cấu hình, **không tự đổi database**. Phải cấu hình URL tương ứng và
kiểm tra database thực tế trước thao tác ghi/xóa. Hiện chưa có script reset/seed tự động.
Không dùng instance demo để chạy test phá dữ liệu.

## Kết nối

1. Cài SQL Server native và Microsoft **ODBC Driver 18 for SQL Server** trên máy chạy
   Backend. Python 3.12 và pyodbc cùng kiến trúc với driver. Linux cần unixODBC/driver
   Microsoft; Windows cài driver bằng installer chính thức.
2. Đầu mối DB tạo database và login/user theo môi trường; tài khoản app chỉ có quyền
   cần cho nghiệp vụ, không mặc định `sa`/sysadmin. Tài khoản migration có quyền DDL
   riêng. Schema và quyền cụ thể được bổ sung cùng migration đã review.
3. Tạo `backend/.env`, điền `HQ_DATABASE_URL` và `HQ_ENV`. SQL Authentication cần bật
   và cấu hình trên SQL Server nếu chọn cách này; Windows integrated auth có thể dùng
   URL không chứa user/password theo ví dụ. Dùng đúng instance/port của máy nhóm.
4. URL-encode ký tự đặc biệt trong user/password. Không in URL ra terminal/log hoặc
   gửi `.env` lên GitHub. `TrustServerCertificate=yes` chỉ dành cho chứng chỉ tự ký
   local; dùng chứng chỉ tin cậy và `no` khi môi trường yêu cầu xác minh chứng chỉ.
5. Từ `backend`: `uv run python -c "import pyodbc; print(pyodbc.drivers())"` rồi chạy
   Backend và gọi `/health/ready`. Kết quả 200 chỉ xác nhận kết nối/`SELECT 1`.

Nguồn chính thức: [Microsoft ODBC](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server),
[SQLAlchemy MSSQL/pyodbc](https://docs.sqlalchemy.org/en/20/dialects/mssql.html#module-sqlalchemy.dialects.mssql.pyodbc).

## Migration

Alembic đã có scaffold ở `backend/migrations`; **chưa có revision nghiệp vụ**.
Từ `backend`, `uv run alembic heads` kiểm tra đồ thị revision mà không cần SQL.
Chỉ chạy `uv run alembic upgrade head` khi đã có migration được review, đúng DB và
credential migration. `HQ_MIGRATION_DATABASE_URL` ưu tiên hơn URL app; với local có
thể tạm dùng chung nhưng demo phải tách quyền.

Khi có migration, kiểm thử trên SQL Server test mới và SQL Server test đã có dữ liệu;
ghi kết quả bảng/cột/ràng buộc và rollback/phục hồi theo kế hoạch. Không chạy migration
như side effect của khởi động API; không gọi `Base.metadata.create_all()` thay versioning.

## API và build FE

Vite dùng proxy cùng origin, không cần bật CORS rộng. URL dev proxy nằm ở
`frontend/vite.config.ts` và không chứa credential. FE dùng đường tương đối `/api/...`;
thiết kế phiên/CSRF được bổ sung theo F01, chưa có trong khung.

Backend phục vụ build FE cùng origin ở demo. `/api/*` và `/health/*` không rơi vào SPA;
asset thiếu trả 404, build chưa có trả 503 tại trang web. Chạy một process, không reload.
Khi dựng monitoring thread phải bổ sung lifespan/start/stop và readiness phản ánh worker
chết; không được giữ readiness chỉ kiểm tra DB rồi tuyên bố toàn hệ thống khỏe.

## Công cụ và nâng dependency

Python 3.12; Node 24; package-lock.json và uv.lock lưu dependency cụ thể.
Cài uv theo [tài liệu chính thức](https://docs.astral.sh/uv/getting-started/installation/).
`uv sync --locked` và `npm ci` là lệnh cài chuẩn. Nâng dependency trong PR riêng, chạy
lại kiểm tra tương ứng; không thay lockfile chỉ để giải quyết conflict bằng cách xóa.
