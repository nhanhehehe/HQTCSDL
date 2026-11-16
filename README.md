# HQTCSDL — Giám sát nông trại IoT

Nền tảng **FastAPI + SQL Server + React/TypeScript** cho hai Node và một Gateway.
Code cũ đã lưu tại `archive/legacy-before-foundation`; khung này bắt đầu từ
`develop` sau cleanup. Thiết kế nghiệp vụ nằm trên Confluence, code/test nằm ở đây.

| Thư mục | Vai trò |
| --- | --- |
| `backend/app/core` | Cấu hình, DB/session, lỗi và log dùng chung |
| `backend/app/modules` | Module theo 03.3; thêm theo luồng đã review |
| `backend/migrations` | Alembic; M1 8 bảng nền tảng, M2 thêm 7 bảng đo/config/runtime |
| `backend/tests` | Kiểm tra cơ chế nền tảng; chưa phải kiểm thử SQL thực tế |
| `frontend/src` | React: `app`, `api`, `features` |
| `database` | Hướng dẫn CSDL và `seed_foundation.sql` (Farm/Zone/Metric) |
| `docs`, `tools` | Hướng dẫn phát triển, kiểm chứng và công cụ tích hợp |

## Bắt đầu

Cần Git, **Python 3.12**, **uv**, **Node.js 24 LTS/npm**. Cài SQL Server native và
Microsoft ODBC Driver 18 khi cần kết nối DB. `pyodbc` không tự cài driver hệ điều hành.

```bash
git clone https://github.com/nhanhehehe/HQTCSDL.git
cd HQTCSDL
git switch develop
cd backend
uv sync --locked
```

Chỉ khi **chưa có** `backend/.env`, tạo file từ `backend/.env.example`.
Giữ nguyên file local nếu đã tồn tại; không copy đè. Có thể chạy liveness chưa
cấu hình SQL; readiness sẽ 503.

Để dựng DB dùng được, làm theo thứ tự:

1. [Tạo DB dev/test, tài khoản app/migration và cấu hình SNAPSHOT](docs/development.md#tao-database-va-cap-quyen).
2. [Điền hai URL và kiểm tra DB/login thực tế](docs/development.md#cau-hinh-backend-va-kiem-tra-dich).
   `HQ_DATABASE_URL` dùng tài khoản app; `HQ_MIGRATION_DATABASE_URL` bắt buộc cho
   Alembic khi kết nối DB hoặc sinh SQL, không fallback sang tài khoản app.
3. [Áp M1 trên DB test trước, chạy seed và kiểm tra kết quả](database/README.md#ap-m1-va-seed).
   Mỗi DB đích cần được migration riêng; đổi nhãn `HQ_ENV` không tự đổi DB.

Kết quả M1: 8 bảng nghiệp vụ + `alembic_version`; seed 1 Farm, 2 Zone, 5 metric.
Seed không tạo Admin; làm theo mục [Tạo tài khoản Admin](#tao-tai-khoan-admin) bên dưới
để đăng nhập. Đổi `backend/.env` thì khởi động lại Backend để nhận cấu hình mới.
Không commit `.env`.

Trong thư mục `backend`:

```bash
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Mở terminal khác, từ gốc repo:

```bash
cd frontend
npm ci
npm run dev
```

Mở http://127.0.0.1:5173. Vite proxy `/api` và `/health` sang Backend.

- API docs: http://127.0.0.1:8000/docs
- `/health/live`: tiến trình API phản hồi.
- `/health/ready`: kiểm tra `SELECT 1` trên SQL Server; 503 khi thiếu cấu hình/lỗi.
  Scope hiện tại là `foundation`, `monitoring=not_implemented`; **chưa chứng minh hệ thống
  giám sát sẵn sàng**. Khi có worker phải bổ sung điều kiện sức khỏe worker.

<a id="tao-tai-khoan-admin"></a>

## Tạo tài khoản Admin

Không có tài khoản/mật khẩu mặc định. Đây là tài khoản đăng nhập web, khác với
SQL login app/migration trong hai URL kết nối.

**Điều kiện:** SQL Server đang chạy, DB đích đã áp M1 (có `AdminAccount` và
`AdminSession`), hai URL trong `backend/.env` cùng trỏ đúng DB dự định sử dụng.
Auth chỉ cần M1; không cần áp M2 để tạo tài khoản. Lệnh tạo Admin dùng
`HQ_MIGRATION_DATABASE_URL`; Backend đăng nhập dùng `HQ_DATABASE_URL`.

1. Mở PowerShell tại gốc repo và cài dependency đã khóa:

   ```powershell
   cd backend
   uv sync --locked
   ```

2. Tạo tài khoản với username mẫu `Admin` trên DB dev của bạn:

   ```powershell
   uv run python -m app.modules.auth.create_admin Admin --database HQTCSDL_dev_ten
   ```

   Thay `ten` bằng tên thành viên theo quy ước tạo DB, hoặc thay toàn bộ
   `HQTCSDL_dev_ten` bằng tên DB thực tế của bạn; thay `Admin` nếu muốn username khác.
   `--database` chỉ
   xác nhận tên DB mong đợi, **không đổi DB trong URL**. Lệnh từ chối ghi nếu
   `DB_NAME()` khác tên này. Nếu dùng terminal tại `backend` có `.venv` đã đồng bộ
   nhưng chưa có `uv` trên PATH, có thể chạy:

   ```powershell
   .\.venv\Scripts\python.exe -m app.modules.auth.create_admin Admin --database HQTCSDL_dev_ten
   ```

3. Nhập mật khẩu tại `Password:` rồi nhập lại ở `Confirm password:`. Ký tự không
   hiện trên màn hình là bình thường. Mật khẩu được lưu bằng hash Argon2id;
   không ghi mật khẩu vào lệnh, README hoặc seed. Username phân biệt hoa/thường;
   mật khẩu giữ nguyên cả khoảng trắng.

4. Khi thấy `Admin created.`, tài khoản đã được commit. Mở
   http://127.0.0.1:5173 và đăng nhập bằng username/mật khẩu vừa chọn. Backend và
   Vite phải đang chạy theo hướng dẫn ở trên.

Để đăng nhập, `.env` cần `HQ_CSRF_SECRET` riêng, ngẫu nhiên và ổn định; giữ nguyên
secret nếu đã có. `HQ_WEB_ORIGIN` mặc định là `http://127.0.0.1:5173`, phải trùng
origin browser, không có dấu `/` cuối. Dùng đúng `127.0.0.1` thay vì đổi sang
`localhost` khi chưa đổi cấu hình. Với build phục vụ ở cổng 8000, đặt
`HQ_WEB_ORIGIN=http://127.0.0.1:8000`. HTTP local dùng `HQ_COOKIE_SECURE=false`;
HTTPS cần `true`. Restart Backend sau khi đổi cấu hình.

| Thông báo / lỗi | Cách xử lý |
| --- | --- |
| `Username exists; password was not changed.` | Tài khoản đã có; dùng mật khẩu cũ hoặc chọn username khác. Lệnh không reset mật khẩu. |
| `Passwords do not match.` | Chạy lại lệnh và nhập hai lần giống nhau. |
| `Database does not match --database` | Kiểm lại DB trong migration URL và tên xác nhận; không chuyển DB tùy tiện. |
| `Storage operation failed` | Chưa xác nhận được tạo tài khoản; kiểm SQL/ODBC/quyền và M1 trước khi thử lại. |
| Đăng nhập báo 403 | Kiểm origin browser, `HQ_WEB_ORIGIN` và restart Backend sau khi sửa. |
| Đăng nhập báo 503 | Kiểm kết nối runtime SQL và `HQ_CSRF_SECRET`; không kết luận mật khẩu sai. |

Chi tiết: [cấu hình auth và kiểm chứng](docs/development.md#t05t06--cấu-hình-và-tài-khoản-quản-trị).

## Kiểm tra trước PR

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run alembic heads
```

Từ `frontend`: `npm run typecheck` và `npm run build`.
`npm ci`/`uv sync --locked` dùng lockfile đã commit; thay dependency cần cập nhật lock
trong cùng PR. CI chạy các kiểm tra này, chưa chạy SQL Server/hardware.

## Bản build phục vụ cùng Backend

Chạy `npm run build` ở `frontend`, rồi chạy Backend như trên. Mở
http://127.0.0.1:8000; FastAPI phục vụ `frontend/dist`. Với máy demo LAN, thay host bằng
`0.0.0.0`, cấu hình firewall và IP theo mục 06. Một API process; không dùng reload
trong demo. Hiện chưa có monitoring worker.

Đọc [hướng dẫn môi trường](docs/development.md), [quy trình nhóm](CONTRIBUTING.md),
[quy ước CSDL](database/README.md) và [bằng chứng kiểm tra](docs/foundation-validation.md).

## Thiết kế áp dụng

- [03.3 — Kiến trúc](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14188568)
- [03.4 — CSDL chung](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14352390)
- [03.5 — Thiết kế luồng](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14319653)
- [03.6 — Nền tảng và quy trình](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14647366)
- [03.1 — Contract Gateway–Backend](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/13729794)

PR #10 đã đưa vào `develop` WebModel strict/cấm trường lạ, UTC clock millisecond,
lỗi web/validation có request ID và M1/seed. T05/T06 bổ sung login/session/logout,
CSRF và màn hình xác thực; response auth thành công/lỗi đều `no-store`.
Helper ID/Decimal/rowversion bổ sung theo từng luồng,
chưa phải bộ codec đầy đủ của draft PR #9 (đã đóng, không merge).

Source có M2 `b15e2c03a001` (tổng 15 bảng nghiệp vụ), chưa có M3/ACK2/worker hoặc
dashboard nghiệp vụ. Việc DB đã áp revision và Admin đã tồn tại phải kiểm riêng;
không tự tạo tài khoản hoặc migrate lúc startup. Xem [cấu hình auth và lệnh tạo Admin](docs/development.md#t05t06--cấu-hình-và-tài-khoản-quản-trị).
Không dùng trang khởi động hoặc test mock làm bằng chứng đạt AC/NFR.
