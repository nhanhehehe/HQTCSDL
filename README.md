# HQTCSDL — Giám sát nông trại IoT

Nền tảng **FastAPI + SQL Server + React/TypeScript** cho hai Node và một Gateway.
Code cũ đã lưu tại `archive/legacy-before-foundation`; khung này bắt đầu từ
`develop` sau cleanup. Thiết kế nghiệp vụ nằm trên Confluence, code/test nằm ở đây.

| Thư mục | Vai trò |
| --- | --- |
| `backend/app/core` | Cấu hình, DB/session, lỗi và log dùng chung |
| `backend/app/modules` | Module theo 03.3; thêm theo luồng đã review |
| `backend/migrations` | Alembic; M1 8 bảng nền tảng, M2 thêm 7 bảng đo/config/runtime |
| `backend/tests` | Unit/API giả lập, SQL migration offline và SQL auth/M2 opt-in |
| `frontend/src` | React: `app`, `api`, `features` |
| `database` | Hướng dẫn CSDL và `seed_foundation.sql` (Farm/Zone/Metric) |
| `docs`, `tools` | Hướng dẫn phát triển, kiểm chứng và công cụ tích hợp |

## Chức năng trong source hiện tại

| Phạm vi | Đã triển khai | Giới hạn |
| --- | --- | --- |
| FL-01 — Auth | Admin CLI, login/session/logout, cookie/CSRF và FE xác thực | Không có tài khoản mặc định, đăng ký web hoặc reset mật khẩu |
| FL-02 — Khu vực | Đọc Farm/Zone theo manifest; trạng thái VALID/INVALID và issues | Không CRUD Farm/Zone; thiếu một Zone không chặn target khác còn hợp lệ |
| FL-03 — Hồ sơ | Tạo/list/detail và sửa tên/ghi chú Node/Gateway; exact-code lookup, cursor, rowversion | Không ghép/chuyển/xóa thiết bị hoặc bật/tắt quyền qua web |
| M1/M2 — CSDL | 15 bảng nghiệp vụ và revision Alembic | Có schema đo/config/runtime chưa có nghĩa ingestion, cấu hình giám sát hay worker đã chạy |

Node mới chưa gắn Gateway vẫn là hồ sơ hợp lệ, hiển thị **Chưa liên kết Gateway**.
Tạo hồ sơ không đợi phần cứng và không chứng minh thiết bị online. Chưa có bản đo
thì mốc cuối lưu là null; không tự tạo số đo, ngưỡng hoặc period giả.

Source hiện tại chưa có M3, cấp credential thiết bị, ingestion/ACK2, API xem số đo,
cấu hình giám sát, worker cảnh báo, dashboard hoặc báo cáo nghiệp vụ. Trạng thái
source trong checkout này không chứng minh tính năng đã merge, đã áp lên mọi DB
hoặc đã nghiệm thu đầy đủ AC/NFR.

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
4. [Áp và kiểm M2 trên DB test](database/README.md#m2--schema-đo-cấu-hình-và-runtime)
   để chạy đầy đủ hồ sơ thiết bị. Auth chỉ cần M1; FL-03 cần cả M2.
5. Tạo Admin riêng trên DB dự định dùng nếu chưa có; đối chiếu cấu hình khu vực
   bên dưới rồi restart Backend. Không tự chạy lại seed nếu dữ liệu nền đã đủ.

Kết quả M1: 8 bảng nghiệp vụ + `alembic_version`; seed 1 Farm, 2 Zone, 5 metric.
Seed không tạo Admin; làm theo mục [Tạo tài khoản Admin](#tao-tai-khoan-admin) bên dưới
để đăng nhập. Đổi `backend/.env` thì khởi động lại Backend để nhận cấu hình mới.
Không commit `.env`.

Backend **không tự chạy migration, seed hay tạo Admin khi khởi động**. Hai URL có
thể trỏ các DB khác nhau; migration thành công trên dev không cập nhật test.
Trước mỗi thao tác DB, đối chiếu server/instance, DB và login thực tế.

### Cấu hình khu vực và phân trang

Chỉ bổ sung biến còn thiếu trong `.env` hiện có; xem
[mẫu cấu hình](backend/.env.example) và
[hướng dẫn FL-02/FL-03](docs/development.md#fl-02fl-03--khu-vực-và-hồ-sơ-thiết-bị).

| Biến | Cách dùng |
| --- | --- |
| `HQ_AREA_MANIFEST` | Chuỗi JSON chọn Farm và hai Zone indoor/outdoor bằng mã chính xác; mã phải tồn tại trong DB runtime |
| `HQ_CURSOR_SECRET` | Secret ngẫu nhiên riêng để ký cursor, khác CSRF secret, giữ ổn định qua restart |
| `HQ_DATASET_GENERATION` | Nhãn dataset, mặc định `1`; đổi sau restore/reset để vô hiệu cursor cũ, không đổi mỗi lần sửa hồ sơ |

Manifest mẫu với seed mặc định:

```dotenv
HQ_AREA_MANIFEST='{"farm_code":"FARM-01","zone_codes":{"indoor":"INDOOR","outdoor":"OUTDOOR"}}'
```

Manifest chỉ chọn khu vực, không ghi đè tên/mô tả trong DB. Thiếu/sai manifest
vẫn cho phép đăng nhập nhưng context trả `200 INVALID`; lỗi truy cập SQL trả 503.
Cấu hình cursor secret trước khi test phân trang; không đưa secret vào FE/log/Git.

### Chạy Backend và Frontend

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

Từ `frontend`:

```bash
npm test
npm run typecheck
npm run build
```

`npm ci`/`uv sync --locked` dùng lockfile đã commit; thay dependency cần cập nhật lock
trong cùng PR. Workflow CI có chạy các kiểm tra này. SQL auth/M2 tests cần opt-in
trên DB test đã chọn; CI thông thường không kiểm SQL Server thật hoặc hardware.

FE giữ form và version hồ sơ khi xác minh lại cùng phiên trên focus/resume. Trong
lúc xác minh hoặc sau lỗi mạng/503, Lưu bị khóa; dùng **Kiểm tra phiên** để thử lại.
401/hết hạn/logout/đổi phiên xóa dữ liệu riêng. Sau mất phản hồi mutation, giữ form
để đối chiếu; không tự retry ghi hoặc dùng version mới để ghi đè. Xem
[cách test hai tab và phiên](docs/development.md#t05t06--cấu-hình-và-tài-khoản-quản-trị).

Test unit/fetch giả lập và build không thay nghiệm thu trình duyệt, SQL rollback,
hai kết nối đồng thời, snapshot/pool và logout/write race. Không diễn giải test
đã có thành toàn bộ AC/NFR đạt; ghi riêng môi trường, lệnh/kết quả và phần chưa chạy.

## Đối chiếu code và tài liệu SDLC

| Nội dung cần đối chiếu | Source | Kiểm chứng hiện có |
| --- | --- | --- |
| Auth/session/CSRF | [Backend auth](backend/app/modules/auth), [FE auth](frontend/src/features/auth) | [API/auth tests](backend/tests/test_auth.py), [session FE tests](frontend/tests/auth-session.test.mjs) |
| Context và hồ sơ thiết bị | [Devices API](backend/app/modules/devices), [Devices FE](frontend/src/features/devices) | [Devices tests](backend/tests/test_devices.py); có mock transaction, chưa thay SQL thật |
| M1/M2 và IDENTITY/FK | [Migrations](backend/migrations/versions), [hướng dẫn CSDL](database/README.md) | [DDL offline](backend/tests/test_m2_migration.py), [SQL auth/M2 opt-in](backend/tests/test_sql_auth_m2.py) |
| Request cũ/401/lỗi mạng | [FE client](frontend/src/api/client.ts) | [Client tests](frontend/tests/api-client.test.mjs) |

Các thiết kế Confluence bên dưới là nguồn nghiệp vụ; README mô tả phạm vi code
và đường dẫn kiểm chứng, không tự sửa contract đã chốt. API đang đăng ký có thể
xem ở `/docs` và `/openapi.json` của Backend. Bằng chứng nền tảng lịch sử ở
[foundation-validation](docs/foundation-validation.md) không thay kết quả nghiệm
thu FL-02/FL-03 mới; cần lưu bằng chứng mới khi cập nhật SDLC.

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
