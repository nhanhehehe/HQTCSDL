# Môi trường và hướng dẫn SQL Server

| Profile `HQ_ENV` | Database gợi ý | Dữ liệu |
| --- | --- | --- |
| `dev` | `HQTCSDL_dev_ten` | Local riêng từng thành viên |
| `test` | `HQTCSDL_test_ten` | Fixture/simulator, tách khỏi đo thật |
| `demo` | `HQTCSDL_demo` | Dữ liệu demo/thiết bị thật |

Thay `ten` bằng tên thành viên, hoặc dùng tên DB riêng đã thống nhất cho môi trường.
`HQ_ENV` chỉ là nhãn, **không tự đổi database**; URL mới
quyết định đích. Hiện có seed SQL thủ công, chưa có runner/reset hoặc guard tự
ngăn ghi nhầm DB. Luôn kiểm tra tên DB/login thực tế trước ghi/xóa.

## Cài đặt và kết nối SQL Server

- Cài SQL Server native và SSMS. Máy đã kiểm chứng của Nhân là SQL Server 2022
  Express; không bắt buộc mọi thành viên dùng đúng tên instance hoặc build đó.
- Cài Microsoft **ODBC Driver 18 for SQL Server** trên máy chạy Backend;
  Python 3.12 và driver cùng kiến trúc. pyodbc không tự cài driver hệ điều hành.
  Linux cần unixODBC và driver Microsoft tương ứng.
- Cài dependency từ `backend/`: `uv sync --locked`. Kiểm driver:
  `uv run python -c "import pyodbc; print(pyodbc.drivers())"`.
- Ghi đúng Server name trong SSMS: ví dụ named instance `DESKTOP-...\SQLEXPRESS`,
  không tự thay bằng `localhost:1433`. SQL Express có thể chưa bật TCP/IP hoặc
  không dùng cổng 1433; xác định instance/protocol/port với người phụ trách DB.
  Với máy khác/LAN, cấu hình TCP/IP và firewall đúng cổng đã chọn trước khi thử.
- SQL login cần server cho phép SQL Authentication (Mixed Mode), login được bật
  và có user trong DB. Windows integrated auth là phương án khác theo `.env.example`;
  tài khoản Windows dùng bởi app/migration vẫn phải có quyền đúng vai trò.

Nguồn: [ODBC Driver 18](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server),
[SQLAlchemy MSSQL/pyodbc](https://docs.sqlalchemy.org/en/20/dialects/mssql.html#module-sqlalchemy.dialects.mssql.pyodbc).

<a id="tao-database-va-cap-quyen"></a>

## Tạo database và cấp quyền

Các lệnh quản trị bên dưới chạy bằng kết nối quản trị SSMS (`sa` hoặc tài khoản
có đủ quyền), không dùng credential đó cho Backend. Đây là mẫu dựng local mới:
đổi tên DB/login và đặt mật khẩu riêng trên máy trước khi thực hiện. Đối chiếu DB,
login/user đã có trước khi tạo; không xóa hoặc đổi mật khẩu tài khoản của người khác.

### 1. Tạo DB dev/test và cấu hình SNAPSHOT

```sql
USE [master];
GO

IF DB_ID(N'HQTCSDL_dev_ten') IS NULL
    EXEC(N'CREATE DATABASE [HQTCSDL_dev_ten]');
GO
IF DB_ID(N'HQTCSDL_test_ten') IS NULL
    EXEC(N'CREATE DATABASE [HQTCSDL_test_ten]');
GO

ALTER DATABASE [HQTCSDL_dev_ten] SET AUTO_CLOSE OFF;
ALTER DATABASE [HQTCSDL_test_ten] SET AUTO_CLOSE OFF;
ALTER DATABASE [HQTCSDL_dev_ten] SET ALLOW_SNAPSHOT_ISOLATION ON;
ALTER DATABASE [HQTCSDL_test_ten] SET ALLOW_SNAPSHOT_ISOLATION ON;
GO

SELECT name, state_desc, collation_name, is_auto_close_on,
       snapshot_isolation_state_desc, is_read_committed_snapshot_on
FROM sys.databases
WHERE name IN (N'HQTCSDL_dev_ten', N'HQTCSDL_test_ten');
```

Mong đợi ONLINE, AUTO_CLOSE=0, SNAPSHOT=ON. DB mới theo profile đã kiểm chứng có
READ_COMMITTED_SNAPSHOT=0; script không tự đổi setting RCSI của DB đã tồn tại.
SNAPSHOT được chọn tường minh cho transaction cần nó, không tự biến mọi SELECT
thành SNAPSHOT. Mã/username dùng collation riêng trong M1, không cần đổi collation
mặc định của DB để ép toàn bộ cột phân biệt hoa/thường.

Nếu ALTER chờ lâu, đối chiếu transaction còn mở trước; không tự restart server
hoặc cưỡng bức ngắt phiên nhóm. Bằng chứng SNAPSHOT hai phiên nằm tại mục kiểm chứng
bên dưới, không chỉ dựa setting ON để kết luận hoạt động.

### 2. Tạo hai login theo vai trò

Local đơn giản dùng chung hai login cho dev/test; user/quyền cấp riêng trong từng
DB. Không cần tạo thêm hai login test. Cách này **không cô lập quyền truy cập giữa
dev và test**: trách nhiệm kiểm đúng DB vẫn thuộc cấu hình/công cụ. Demo/đo thật
có cấu hình và phạm vi truy cập được người phụ trách cấp riêng.

Chỉ chạy phần CREATE LOGIN nếu login tương ứng chưa tồn tại; thay hai password
placeholder, không dùng chúng làm mật khẩu thật và không commit bản SQL đã điền secret:

```sql
USE [master];
GO
CREATE LOGIN [hqtcsdl_app_ten]
WITH PASSWORD = N'REPLACE_LOCAL_APP_PASSWORD', CHECK_POLICY = ON;
GO
CREATE LOGIN [hqtcsdl_migration_ten]
WITH PASSWORD = N'REPLACE_LOCAL_MIGRATION_PASSWORD', CHECK_POLICY = ON;
GO
```

### 3. Tạo user và cấp quyền trong từng DB

Chạy block sau cho DB dev; sau đó đổi dòng USE thành `HQTCSDL_test_ten` và chạy lại
cùng block để cấp quyền trên DB test. `USE` chỉ chọn DB, không đổi login.

```sql
USE [HQTCSDL_dev_ten];
GO

IF USER_ID(N'hqtcsdl_app_ten') IS NULL
    CREATE USER [hqtcsdl_app_ten]
    FOR LOGIN [hqtcsdl_app_ten] WITH DEFAULT_SCHEMA = [dbo];

IF USER_ID(N'hqtcsdl_migration_ten') IS NULL
    CREATE USER [hqtcsdl_migration_ten]
    FOR LOGIN [hqtcsdl_migration_ten] WITH DEFAULT_SCHEMA = [dbo];
GO

GRANT CONNECT TO [hqtcsdl_app_ten];
GRANT SELECT, INSERT, UPDATE, DELETE
ON SCHEMA::[dbo] TO [hqtcsdl_app_ten];

GRANT CONNECT, CREATE TABLE TO [hqtcsdl_migration_ten];
GRANT ALTER, REFERENCES, SELECT, INSERT, UPDATE, DELETE
ON SCHEMA::[dbo] TO [hqtcsdl_migration_ten];
GO
```

Mẫu dành cho login/user mới; nếu user đã có nhưng ánh xạ login khác, cần kiểm tra
SID trước, không coi IF USER_ID là bằng chứng mapping đúng. Script không thu hồi
quyền rộng đã được cấp trước đó; phải kiểm tra hiệu lực thực tế ở bước 4.

| Vai trò | Quyền dự kiến trên DB đích |
| --- | --- |
| App | Đọc/thêm/sửa/xóa trên dbo; không CREATE TABLE/ALTER schema |
| Migration | Tạo/sửa/xóa bảng/ràng buộc trên dbo và đọc/ghi seed/version |
| Quản trị | Dựng DB/login/user/setting, không dùng chạy Backend thường ngày |

Không cấp sysadmin/db_owner cho app/migration. Quyền UPDATE/DELETE của app không
thay quy tắc nghiệp vụ: dữ liệu đo gốc phải bất biến theo đường ghi đã thiết kế.
Quyền migration hiện đủ cho M1 bảng/FK/CHECK/index; nếu sau này thêm procedure/view
phải review và cấp đúng quyền cần thêm, không mặc định nâng thành quản trị.

### 4. Kiểm tra bằng kết nối app và migration riêng

Mở New Query từ đúng kết nối; chọn kết nối khác ở Object Explorer không đổi login
của Query đã mở. Chạy trên cả DB dev/test bằng từng login:

```sql
-- Chọn DB đích bằng dropdown SSMS hoặc USE [ten_db]; trước khi chạy.
SELECT SUSER_SNAME() AS login_name, USER_NAME() AS database_user,
       DB_NAME() AS database_name, IS_SRVROLEMEMBER(N'sysadmin') AS is_sysadmin,
       HAS_PERMS_BY_NAME(N'dbo', N'SCHEMA', N'SELECT') AS can_select,
       HAS_PERMS_BY_NAME(N'dbo', N'SCHEMA', N'INSERT') AS can_insert,
       HAS_PERMS_BY_NAME(N'dbo', N'SCHEMA', N'UPDATE') AS can_update,
       HAS_PERMS_BY_NAME(N'dbo', N'SCHEMA', N'DELETE') AS can_delete,
       HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'CREATE TABLE') AS can_create_table,
       HAS_PERMS_BY_NAME(N'dbo', N'SCHEMA', N'ALTER') AS can_alter_schema;
```

App: bốn quyền DML=1, hai quyền DDL=0; migration: sáu quyền=1; cả hai sysadmin=0.
Đây là kiểm quyền, chưa thay kiểm đọc/ghi/rollback thực tế.

<a id="cau-hinh-backend-va-kiem-tra-dich"></a>

## Cấu hình Backend và kiểm tra đích

Tạo `backend/.env` từ `.env.example`. Bỏ dấu # của biến cần dùng, thay các giá trị
placeholder bằng cấu hình local. Không gửi file hoặc URL thật lên chat/Git.

| Biến | Ý nghĩa |
| --- | --- |
| `HQ_ENV` | Nhãn dev/test/demo, không tự chọn database |
| `HQ_DATABASE_URL` | Kết nối runtime app, dùng login app |
| `HQ_MIGRATION_DATABASE_URL` | Kết nối Alembic, bắt buộc khi thao tác cần env.py; dùng login migration |

Không có fallback từ migration URL sang app URL nữa. Hai URL đều là
`mssql+pyodbc`, có DB tường minh. Mẫu `.env.example` dùng localhost:1433 làm ví dụ;
đổi server/instance/port theo kết nối thực tế đã kiểm chứng.
URL-encode ký tự đặc biệt trong username/password. Không in URL ra terminal/log;
`TrustServerCertificate=yes` chỉ dành cho chứng chỉ tự ký local, môi trường yêu cầu
xác minh chứng chỉ phải dùng chứng chỉ tin cậy và cấu hình tương ứng.

Kiểm tra kết nối app từ `backend/`, chỉ in tên DB/login thực tế:

```powershell
uv run python -c "from app.core.config import Settings; from sqlalchemy import create_engine, text; s=Settings(); e=create_engine(s.database_url.get_secret_value(), hide_parameters=True); c=e.connect(); print(c.execute(text('SELECT DB_NAME(), SUSER_SNAME()')).one()); c.close(); e.dispose()"
```

Lặp lại bằng `s.migration_database_url` thay `s.database_url` để kiểm đích Alembic.
Runtime app có thể trỏ dev trong lúc migration đang thử trên test, nhưng phải xác
nhận rõ hai đích trước khi chạy; một URL đúng không chứng minh URL còn lại đúng.
Sau khi thay `.env`, khởi động lại Backend đang chạy để nạp cấu hình mới.
Gọi `/health/ready` sau khi chạy API: 200 chỉ chứng minh kết nối/SELECT 1, không
chứng minh đã có bảng, migration, worker hoặc quyền ghi nghiệp vụ.

## Migration và seed

Thực hiện [hướng dẫn M1/seed](../database/README.md#ap-m1-va-seed): xác nhận DB test
và login migration → heads/current → xem SQL → upgrade → kiểm 9 bảng/revision →
seed SSMS → kiểm chạy lại 1/2/5. Downgrade xóa dữ liệu, chỉ kiểm trên DB test được
phép bỏ dữ liệu. Source có M1 và M2; M3 chưa triển khai. Không tự áp migration lúc API
khởi động; không chạy `create_all()` thay versioning.

## Kiểm chứng SQL trên máy thành viên

Mỗi máy ghi lại OS/build/SQL instance/ODBC, tên DB và quyền; không ghi secret.
Đã có bằng chứng máy Nhân ở [foundation-validation.md](foundation-validation.md),
không coi máy khác đã đạt chỉ vì source giống nhau. Có thể dùng một bảng thử riêng
trên DB test để kiểm tra như trình tự đã dùng:

1. Migration tạo bảng thử và dòng giá trị 100.
2. App UPDATE/INSERT rồi ROLLBACK: thấy giá trị ban đầu; INSERT/DELETE có COMMIT
   được lưu như kỳ vọng. App CREATE TABLE phải bị từ chối.
3. SNAPSHOT hai phiên có SPID khác nhau: A BEGIN và đọc 100; B cập nhật 200 và COMMIT;
   A đọc trong cùng transaction vẫn 100, COMMIT rồi đọc thấy 200. Đặt lại isolation
   READ COMMITTED khi kết thúc. Chỉ chạy từng phần tiếp theo của A, không chạy lại
   BEGIN gây transaction lồng nhau.
4. Xác nhận `@@TRANCOUNT=0` trong từng tab thử, dọn bảng thử bằng migration trên DB
   test. Nếu DDL chờ/lỗi 1222, xem cách xử lý ở [hướng dẫn CSDL](../database/README.md#khi-ssms-hiển-thị-cũ-hoặc-drop-table-bị-chờ).

Không thêm bảng thử vào revision M1. Kiểm chứng SQL trực tiếp và migration Alembic
không thay test transaction/race/rollback của từng tính năng qua Python.

## API và build FE

Vite dùng proxy cùng origin, không cần bật CORS rộng. URL dev proxy nằm ở
`frontend/vite.config.ts` và không chứa credential. FE dùng đường tương đối `/api/...`;
phiên/CSRF dùng ba endpoint `/api/v1/auth/login`, `/session`, `/logout` theo FL-01 v8.

### T05/T06 — cấu hình và tài khoản quản trị

Giữ nguyên `.env` local; chỉ bổ sung biến còn thiếu sau khi đối chiếu. Không copy mẫu
đè lên file đang có. `HQ_WEB_ORIGIN` mặc định `http://127.0.0.1:5173`, phải khớp chính
xác origin của FE, không có dấu `/` cuối. Khi dùng build do Backend phục vụ, cấu hình
origin theo URL Backend. `HQ_COOKIE_SECURE=false` chỉ cho HTTP local/LAN; HTTPS phải
đặt `true`. Thay cấu hình cần restart Backend.

Auth cần `HQ_CSRF_SECRET` riêng, ngẫu nhiên và giữ ổn định qua restart. Tái sử dụng
secret local nếu đã có; không commit, in hoặc đưa secret vào frontend. Không có
secret/DB thì auth không cấp phiên. `HQ_AUTH_TEST_DATABASE_URL` là URL SQL Server
test tùy chọn, không tự thay thế URL runtime hoặc migration.

Lệnh tạo Admin chạy riêng từ `backend`, dùng migration URL; không chạy lúc startup:

```powershell
uv run python -m app.modules.auth.create_admin Admin --database HQTCSDL_dev_ten
```

Thay `HQTCSDL_dev_ten` bằng tên DB thực tế của bạn và `Admin` bằng username muốn tạo.
`--database` xác nhận đích, không thay DB trong `HQ_MIGRATION_DATABASE_URL`.
Lệnh hỏi password và xác nhận bằng `getpass`, kiểm và hiển thị DB/login trước ghi.
Username đã có bị từ chối; không thay password cũ. Chỉ chạy khi đã chủ ý chọn DB và
cho phép tạo tài khoản; seed nền tảng không chứa Admin/password.

Login gửi JSON username/password, `Origin` đúng cấu hình và `X-HQ-Client: web`.
Logout gửi `Origin`, thêm `X-CSRF-Token` của phiên đang valid. Cookie `hq_session`
HttpOnly/host-only/SameSite=Strict/Path=/api; session hết hạn tuyệt đối sau hai giờ
theo giờ SQL, không gia hạn khi đọc. ID trả string, timestamps UTC Z millisecond.
Mọi response auth có `Cache-Control: no-store`.

FE giữ thông tin phiên/CSRF trong memory; không lưu token/password vào localStorage.
Sau lỗi mạng login, dùng **Kiểm tra phiên** để xác minh cookie browser; không tự gửi
lại mutation. Logout chưa được xác nhận sẽ xóa nội dung riêng và cho phép người dùng
chủ động thử lại. Khi hết hạn/401/focus/resume, nội dung riêng phải qua xác minh phiên.

### Kiểm tra auth và M2

Các check Backend thông thường không kết nối SQL. Frontend có bộ kiểm API client nhỏ
dùng Node có sẵn: `node --test tests/api-client.test.mjs` từ `frontend`.

Hai ca SQL trong `backend/tests/test_sql_auth_m2.py` chỉ chạy khi người vận hành bật
cờ. Phải tự áp M2 vào DB test trước, dùng URL test có quyền DML, xác nhận DB/login;
không dùng DB dev/demo chứa dữ liệu cần giữ. Ví dụ từ `backend`:

```powershell
$env:HQ_RUN_SQL_TESTS = '1'
$env:HQ_EXPECT_TEST_DATABASE = 'HQTCSDL_test_ten'
uv run pytest tests/test_sql_auth_m2.py -v -s
Remove-Item Env:HQ_RUN_SQL_TESTS
Remove-Item Env:HQ_EXPECT_TEST_DATABASE
```

Fixture tạo hồ sơ mang mã `test-<uuid>`, runtime/state và dữ liệu đo có nhãn trong một
transaction ngoài, rồi rollback; không migration, seed, commit hay xóa dữ liệu cũ.
Identity có thể có khoảng trống sau rollback. Test này không thay kiểm race hai kết
nối hoặc phép upgrade/downgrade thật. Browser cookie/proxy, focus/resume, expiry và
logout mất response phải kiểm riêng; build/test client không chứng minh các ca đó.

Benchmark là tùy chọn, không chặn triển khai:
`uv run python -m app.modules.auth.benchmark --iterations 20`.
Công cụ chỉ in môi trường/thời gian,
không kết nối SQL và không đọc/in password hay hash. Không suy NFR đạt từ phép đo này.

Backend phục vụ build FE cùng origin ở demo. `/api/*` và `/health/*` không rơi vào SPA;
asset thiếu trả 404, build chưa có trả 503 tại trang web. Chạy một process, không reload.
Khi dựng monitoring thread phải bổ sung lifespan/start/stop và readiness phản ánh worker
chết; không được giữ readiness chỉ kiểm tra DB rồi tuyên bố toàn hệ thống khỏe.

## Công cụ và nâng dependency

Python 3.12; Node 24; package-lock.json và uv.lock lưu dependency cụ thể.
Cài uv theo [tài liệu chính thức](https://docs.astral.sh/uv/getting-started/installation/).
`uv sync --locked` và `npm ci` là lệnh cài chuẩn. Nâng dependency trong PR riêng, chạy
lại kiểm tra tương ứng; không thay lockfile chỉ để giải quyết conflict bằng cách xóa.
