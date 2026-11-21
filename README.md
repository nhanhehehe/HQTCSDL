# HQTCSDL — Nền tảng giám sát dữ liệu IoT nông trại

Ứng dụng quản lý khu vực, thiết bị và dữ liệu từ cảm biến nông trại. **Backend:** Python/FastAPI · **CSDL:** SQL Server/Alembic · **Frontend:** React/TypeScript/Vite.

Luồng dữ liệu: **Node cảm biến → Gateway (LoRa) → HTTP API → SQL Server → Web**. Gateway Simulator có thể gửi dữ liệu mẫu qua HTTP để kiểm thử phần mềm khi chưa kết nối phần cứng. Hệ thống hướng đến mô hình **2 Node / 1 Gateway**, theo dõi 5 đại lượng: nhiệt độ, độ ẩm không khí, áp suất, ánh sáng và độ ẩm đất. UAV là hướng mở rộng, **chưa triển khai** trong source.

## Chức năng hiện có

| Nhóm | Chức năng |
| --- | --- |
| Xác thực | Đăng nhập/đăng xuất Admin, session cookie HttpOnly và CSRF |
| Quản trị | Tạo/xem/sửa Farm, Zone, Gateway, Node; archive/restore khu vực; cấp/thu hồi Gateway API key |
| Thu thập | Nhận batch số đo qua `POST /api/v1/ingestion/measurements`; chống trùng, ACK2, retry bằng Gateway Simulator |
| Dữ liệu | Số đo hiện tại, lịch sử, API chuỗi dữ liệu, độ mới (freshness) |
| Dashboard | Tổng quan Farm/Node và số đo; **chưa có cảnh báo** |
| Báo cáo | Thống kê 5 đại lượng theo khoảng thời gian, xuất CSV theo snapshot |

**Chưa hoàn thành:** cấu hình ngưỡng, worker giám sát/cảnh báo (G4) và thử nghiệm tích hợp phần cứng ESP32/LoRa end-to-end. Màn hình Cảnh báo/Giám sát hiện là placeholder; API chuỗi biểu đồ đã có nhưng giao diện biểu đồ chưa triển khai. Simulator **không thay thế** kiểm thử phần cứng thật.

## Chạy local trên Windows (PowerShell)

**Cần cài:** Git, Python **3.12**, [uv](https://docs.astral.sh/uv/getting-started/installation/), Node.js **24**/npm, SQL Server (Express hoặc Developer), SSMS và Microsoft **ODBC Driver 18 for SQL Server**.

### 1. Clone source

```powershell
git clone https://github.com/nhanhehehe/HQTCSDL.git
cd HQTCSDL
git switch develop
```

### 2. Tạo SQL Server database

Mở **SSMS**, kết nối instance SQL Server local bằng tài khoản quản trị. Ví dụ với SQL Express: `.\SQLEXPRESS`. Bật **SQL Server and Windows Authentication mode** nếu dùng SQL login. Chạy SQL sau **một lần** trên môi trường local mới; thay hai mật khẩu placeholder bằng mật khẩu mạnh, khác nhau:

```sql
USE master;
GO
IF DB_ID(N'HQTCSDL_local') IS NULL CREATE DATABASE [HQTCSDL_local];
GO
ALTER DATABASE [HQTCSDL_local] SET AUTO_CLOSE OFF;
ALTER DATABASE [HQTCSDL_local] SET ALLOW_SNAPSHOT_ISOLATION ON;
GO
IF SUSER_ID(N'hqtcsdl_app_local') IS NULL
    CREATE LOGIN [hqtcsdl_app_local]
    WITH PASSWORD = N'REPLACE_WITH_APP_PASSWORD', CHECK_POLICY = ON;
IF SUSER_ID(N'hqtcsdl_migration_local') IS NULL
    CREATE LOGIN [hqtcsdl_migration_local]
    WITH PASSWORD = N'REPLACE_WITH_MIGRATION_PASSWORD', CHECK_POLICY = ON;
GO
USE [HQTCSDL_local];
GO
IF USER_ID(N'hqtcsdl_app_local') IS NULL
    CREATE USER [hqtcsdl_app_local] FOR LOGIN [hqtcsdl_app_local];
IF USER_ID(N'hqtcsdl_migration_local') IS NULL
    CREATE USER [hqtcsdl_migration_local] FOR LOGIN [hqtcsdl_migration_local];
GO
GRANT CONNECT TO [hqtcsdl_app_local];
GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::dbo TO [hqtcsdl_app_local];

GRANT CONNECT, CREATE TABLE TO [hqtcsdl_migration_local];
GRANT ALTER, REFERENCES, SELECT, INSERT, UPDATE, DELETE
    ON SCHEMA::dbo TO [hqtcsdl_migration_local];
GO
```

Hai login có vai trò riêng: **app** truy cập dữ liệu, **migration** quản lý schema. Không dùng tài khoản `sa` trong Backend.

### 3. Tạo và điền `backend/.env`

Từ thư mục gốc repo:

```powershell
cd backend
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -c "import secrets; [print(secrets.token_urlsafe(48)) for _ in range(3)]"
```

Lệnh cuối sinh **3 secret ngẫu nhiên khác nhau**. Mở `backend/.env`, giữ hoặc bổ sung các dòng sau (thay password và secret, bỏ dấu `#` nếu có):

```dotenv
HQ_ENV=dev
HQ_WEB_ORIGIN=http://127.0.0.1:5173
HQ_COOKIE_SECURE=false

HQ_DATABASE_URL=mssql+pyodbc://hqtcsdl_app_local:APP_PASSWORD_URL_ENCODED@localhost\SQLEXPRESS/HQTCSDL_local?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes
HQ_MIGRATION_DATABASE_URL=mssql+pyodbc://hqtcsdl_migration_local:MIGRATION_PASSWORD_URL_ENCODED@localhost\SQLEXPRESS/HQTCSDL_local?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes

HQ_CSRF_SECRET=PASTE_RANDOM_SECRET_1
HQ_CURSOR_SECRET=PASTE_RANDOM_SECRET_2
HQ_REPORT_SECRET=PASTE_RANDOM_SECRET_3
HQ_DATASET_GENERATION=1
HQ_REPORT_SNAPSHOT_TTL_SECONDS=900
HQ_NODE_TIME_PROFILES={"NODE-SIM-01":{"sampling_interval_seconds":60,"normal_delivery_budget_seconds":300},"NODE-SIM-02":{"sampling_interval_seconds":60,"normal_delivery_budget_seconds":300}}
```

- Thay `localhost\SQLEXPRESS` bằng **Server name thật trong SSMS**; nếu SQL Server chạy cổng cố định `1433`, có thể dùng `localhost:1433`. SQL Express named instance không mặc định chạy cổng 1433.
- Mật khẩu trong URL phải **percent-encode** các ký tự đặc biệt (ví dụ `@` → `%40`, `#` → `%23`). `HQ_ENV` là nhãn môi trường, **không tự chọn DB**.
- `HQ_CSRF_SECRET` bảo vệ phiên, `HQ_CURSOR_SECRET` ký cursor phân trang, `HQ_REPORT_SECRET` ký snapshot báo cáo (tối thiểu 32 byte). Giữ ổn định qua restart và **không commit `.env`**.
- `HQ_NODE_TIME_PROFILES` đặt chu kỳ đo và thời gian giao dự kiến theo đúng **mã Node**; có thể sửa sau khi tạo Node. Thiếu profile thì độ mới có thể hiển thị `PROFILE_MISSING`.
- `TrustServerCertificate=yes` chỉ dành cho local với chứng chỉ tự ký. Không copy đè `.env` đang sử dụng.

### 4. Migration, seed và tạo Admin

Vẫn tại `backend/`:

```powershell
uv sync --locked
uv run alembic upgrade head
uv run alembic current
```

`alembic current` cần hiển thị `fee0fa015af7` (M1 → M2 → bổ sung vòng đời Farm/Zone). **Backend không tự migrate hay seed khi khởi động.**

**Seed nền tảng:** trong SSMS, chọn DB `HQTCSDL_local`, mở file `database/seed_foundation.sql` (ở thư mục repo) và nhấn **Execute/F5**. Trên DB mới, seed tạo **1 Farm** `FARM-01`, **2 Zone** `INDOOR` / `OUTDOOR` và **5 MetricDefinition**; chưa tạo thiết bị, số đo hay Admin.

Tạo tài khoản Admin (trong `backend/`):

```powershell
uv run python -m app.modules.auth.create_admin Admin --database HQTCSDL_local
```

Nhập mật khẩu và xác nhận khi được hỏi. `--database` chỉ **kiểm tra tên DB**, không thay URL kết nối; lệnh sử dụng `HQ_MIGRATION_DATABASE_URL`.

### 5. Khởi chạy Backend và Frontend

**Terminal 1 — Backend** (trong `backend/`):

```powershell
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

**Terminal 2 — Frontend** (từ thư mục gốc repo):

```powershell
cd frontend
npm ci
npm run dev
```

| Địa chỉ | Mục đích |
| --- | --- |
| http://127.0.0.1:5173 | Web app; đăng nhập bằng Admin đã tạo |
| http://127.0.0.1:8000/docs | Swagger API |
| http://127.0.0.1:8000/health/ready | Kiểm tra kết nối SQL (`ready` nếu DB kết nối được) |

Frontend dùng Vite proxy `/api` sang Backend, không cần cấu hình URL FE riêng. Dùng **127.0.0.1**, tránh đổi sang `localhost` khi chưa chỉnh `HQ_WEB_ORIGIN`.

## Tạo dữ liệu demo để thử Dashboard / Report

### 6. Đăng ký Gateway, Node và lấy API key

Sau khi đăng nhập web:

1. Vào **Farm & Zone**: kiểm tra `FARM-01`, `INDOOR`, `OUTDOOR` từ seed.
2. Vào **Thiết bị & Khóa → Gateways & API Keys**: tạo Gateway mã **`GW-SIM-01`**, thuộc `FARM-01`; mở chi tiết Gateway và bấm **Cấp khóa mới**. **Sao chép API key** (`hqgw_v1_...`) ngay vì chỉ hiện một lần.
3. Trong tab **Nodes**, tạo `NODE-SIM-01` tại `INDOOR` và `NODE-SIM-02` tại `OUTDOOR`, đều thuộc Farm trên.

**Liên kết Node–Gateway:** bước này hiện là provisioning kỹ thuật qua SQL, **chưa có nút ghép cặp trên Web**. Trong SSMS, chọn đúng DB local và chạy:

```sql
USE [HQTCSDL_local];
GO
SET XACT_ABORT ON;
BEGIN TRANSACTION;
UPDATE n
SET n.gateway_id = g.gateway_id
FROM dbo.Node AS n
JOIN dbo.Zone AS z ON z.zone_id = n.zone_id
JOIN dbo.Gateway AS g
    ON g.farm_id = z.farm_id AND g.gateway_code = 'GW-SIM-01'
WHERE n.device_code IN ('NODE-SIM-01', 'NODE-SIM-02');

IF @@ROWCOUNT <> 2
BEGIN
    ROLLBACK TRANSACTION;
    THROW 50001, N'Phải tạo đủ 2 Node và 1 Gateway cùng Farm trước khi liên kết.', 1;
END;
COMMIT TRANSACTION;

SELECT n.device_code, g.gateway_code
FROM dbo.Node AS n
LEFT JOIN dbo.Gateway AS g ON g.gateway_id = n.gateway_id
WHERE n.device_code IN ('NODE-SIM-01', 'NODE-SIM-02');
```

Kết quả mong đợi: **cả 2 Node** đều liên kết `GW-SIM-01`. Đừng thực hiện provisioning mẫu trên DB có dữ liệu thật.

### 7. Gửi số đo mẫu bằng Gateway Simulator

**Terminal 3** tại **gốc repo `HQTCSDL/`**, trong khi Backend vẫn chạy. Dán API key vừa cấp vào biến môi trường terminal (không lưu key trong code hoặc commit):

```powershell
$env:HQ_SIM_API_KEY = "PASTE_GATEWAY_API_KEY"
$queue = Join-Path $env:TEMP "hqtcsdl-sim-queue.json"

uv --project backend run python -m tools.gateway_simulator.cli --state $queue enqueue --device-id NODE-SIM-01 --boot-id 1 --seq 1
uv --project backend run python -m tools.gateway_simulator.cli --state $queue enqueue --device-id NODE-SIM-01 --boot-id 1 --seq 2
uv --project backend run python -m tools.gateway_simulator.cli --state $queue enqueue --device-id NODE-SIM-02 --boot-id 1 --seq 1

uv --project backend run python -m tools.gateway_simulator.cli --state $queue sync --base-url http://127.0.0.1:8000 --gateway-id GW-SIM-01
uv --project backend run python -m tools.gateway_simulator.cli --state $queue status
Remove-Item Env:HQ_SIM_API_KEY
```

Kết quả mong đợi: `http_status=200`, số bản ghi được giải phóng khỏi queue; `pending=0`, `quarantine=0`. Mẫu Simulator có đủ **5 đại lượng**; lệnh `sync` chỉ gửi **một batch** từ queue. Muốn tạo thêm bản ghi, tăng `seq` (giữ `boot-id`) hoặc dùng boot mới; không tái sử dụng cùng identity với dữ liệu khác.

### 8. Kiểm tra trên Web

Chọn **`FARM-01` trong bộ chọn Farm ở Topbar** (Web không tự chọn Farm đầu tiên), sau đó:

| Trang | Kiểm tra |
| --- | --- |
| **Tổng quan** (`/dashboard`) | Danh sách Node, số đo mới nhất, freshness; chỉ số cảnh báo vẫn “Chờ G4” |
| **Dữ liệu đo** (`/measurements`) | Chọn Node, xem 5 chỉ số và lịch sử các `seq` đã gửi |
| **Báo cáo** (`/reports`) | Chọn Node/khoảng thời gian chứa số đo vừa gửi → **Tạo báo cáo** → **Xuất CSV** |
| **Thiết bị & Khóa** (`/devices`) | Xem hồ sơ Node/Gateway, liên kết Gateway và danh sách credential |

Báo cáo mặc định truy vấn dữ liệu theo **giờ đo hợp lệ** (`t_node`). Các số đo tạo bằng Simulator với `time_status=VALID` có thể xuất hiện trong báo cáo; số đo `UNKNOWN` được truy vấn riêng và không đưa vào thống kê này.

## Kiểm tra nhanh & lưu ý

Từ `backend/`:

```powershell
uv run ruff check .
uv run pytest
```

Từ `frontend/`:

```powershell
npm test
npm run typecheck
npm run build
```

- `/health/ready` trả `503`: kiểm SQL Server/ODBC Driver 18, tên instance, mật khẩu và `HQ_DATABASE_URL`.
- Migrate lỗi: kiểm `HQ_MIGRATION_DATABASE_URL` và quyền migration; **không dùng `alembic stamp head` để bỏ qua lỗi**.
- Đăng nhập lỗi: kiểm Admin đã được tạo trên đúng DB, `HQ_CSRF_SECRET` và `HQ_WEB_ORIGIN`.
- Simulator trả `401/403`: kiểm API key, mã Gateway trùng hồ sơ, key chưa bị thu hồi; bản ghi bị từ chối: kiểm Node đã liên kết Gateway, quyền nhận và mã Node.
- Thay `backend/.env` cần **restart Backend**. Queue Simulator nằm trong thư mục TEMP local, không phải seed SQL hay dữ liệu thiết bị thật.

## Cấu trúc source

```text
backend/          FastAPI, module nghiệp vụ, Alembic, test
frontend/         React/Vite: đăng nhập, quản trị, Dashboard, dữ liệu, báo cáo
database/         Seed nền tảng và SQL Server
tools/            Gateway Simulator CLI
docs/             Ghi chú phát triển, thiết kế UI và kết quả kiểm chứng
```

**Nhánh phát triển:** `develop`; thay đổi đi qua Pull Request. `main` dành cho phiên bản đã tích hợp/được duyệt.
