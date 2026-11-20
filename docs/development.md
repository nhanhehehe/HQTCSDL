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
phép bỏ dữ liệu. Source có M1, M2 và [PR-A Farm/Zone](../database/README.md#pr-a--cột-vòng-đời-farmzone)
(`fee0fa015af7` sau M2) và [M3](../database/README.md#ap-m3) (`d6b3a5f09c42`).
PR-A chỉ thêm bốn cột, không chuyển đổi manifest hoặc API.
[Bằng chứng kiểm thử PR-A](farm-zone-lifecycle-validation.md) tách
offline tests khỏi upgrade SQL thật và các ca chưa chạy. Không tự áp migration lúc API
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
chủ động thử lại. Hết hạn hoặc nhận 401 sẽ xóa phiên và dữ liệu riêng.

FE không gọi kiểm tra session theo focus/visibilitychange. Kiểm tra lúc mở ứng
dụng và qua nút Kiểm tra phiên; mỗi request nghiệp vụ được Backend xác thực trực
tiếp, không thêm một request session trước mỗi thao tác. Phiên bị thu hồi ở nơi
khác có thể còn hiển thị dữ liệu cũ đến request tiếp theo; đây là đánh đổi đã chốt.
Timer FE vẫn chặn phiên hết hạn theo expires_at đã nhận.
Xác minh thủ công lại cùng phiên giữ nguyên form thiết bị, hồ sơ đã chọn
và rowversion đang giữ; không tự tải bản mới để ghi đè dữ liệu đang nhập. Nút Lưu
bị khóa trong lúc xác minh và sau lỗi mạng/503. Dùng **Kiểm tra phiên** để thử lại;
chỉ phiên được xác minh hợp lệ mới mở lại thao tác ghi. Hết hạn/401/logout xóa
dữ liệu riêng. Nếu cookie chuyển sang phiên khác, form của phiên cũ bị bỏ.

Kiểm tra hồi quy FE từ `frontend/`: `npm test` chạy API client và vòng đời phiên,
gồm revalidation, 401, expiry, response cũ, đổi phiên và logout chưa xác nhận.
CI frontend cũng chạy lệnh này. Các test dùng fetch giả lập, không ghi SQL Server.

Test web thủ công: hai tab cùng tải chi tiết một Node, A đổi tên và Lưu; quay về B
phải giữ dữ liệu/version cũ, không gọi session chỉ vì chuyển tab. B đổi tên và
Lưu phải nhận 409 VERSION_CONFLICT; form giữ bản đang nhập, không tự ghi đè A.
Dùng **Kiểm tra lại hồ sơ, giữ biểu mẫu** để đối chiếu, rồi chủ động chọn hồ sơ
để nạp bản mới. Thử thêm xác minh lỗi mạng: form còn, Lưu khóa; có mạng lại và
Kiểm tra phiên thành công thì Lưu mở lại. Test browser và SQL thật vẫn là bước
nghiệm thu riêng, không suy từ Node tests/typecheck/build.

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

## FL-02/FL-03 — khu vực, hồ sơ thiết bị và khóa Gateway

Farm/Zone được quản lý trực tiếp trong SQL Server (`/api/v1/farms`,
`/api/v1/zones`); `HQ_AREA_MANIFEST` đã được loại bỏ. DB trống Farm vẫn cho
phép đăng nhập để tạo Farm mới. `GET /api/v1/context?farm_id={id}` chỉ là
adapter tương thích chỉ đọc (`0` Farm active → `INVALID/AREA_NOT_CONFIGURED`,
`1` Farm active → `VALID`, `>1` Farm active thiếu `farm_id` → `409
AREA_SELECTION_REQUIRED`). Không tự chạy seed, migration hoặc tạo tài khoản khi khởi động.

Cấu hình `HQ_CURSOR_SECRET` bằng một secret ngẫu nhiên riêng, bền qua restart,
khác CSRF secret. Đổi `HQ_DATASET_GENERATION` sau restore/reset dataset. Danh sách
Farm/Zone phân trang mặc định 20/tối đa 100; hồ sơ thiết bị và credential mặc định
50/tối đa 200; cursor ràng buộc route/filter/page size/order/generation. Mỗi trang
là snapshot riêng. Thiếu cursor secret chỉ được phát hiện khi cần phát hành/xác
minh cursor; cấu hình trước khi vận hành.

Sau đăng nhập: quản lý Farm/Zone (tạo, sửa tên/mô tả, `archive`/`restore` theo
thứ tự lá trước — `409 AREA_IN_USE` nếu còn thiết bị bật quyền nhận); tạo
Gateway chọn `farm_id` tường minh, tạo Node chọn `zone_id` thuộc Farm active,
cấp/thu hồi khóa Gateway (`/api/v1/gateways/{id}/credentials`, tối đa 2 khóa
active, secret `hqgw_v1_...` chỉ hiển thị 1 lần sau commit) và xem danh mục
chỉ đọc `GET /api/v1/metrics`. Node mới có Gateway NULL, không phải bằng chứng
offline. Không có web API ghép/chuyển/xóa hoặc bật/tắt quyền nhận (FL-04 thuộc G4).
Mất phản hồi giữ biểu mẫu; dùng kiểm tra lại để đối chiếu, không tự ghi đè version
hay tự cấp tiếp khóa.

GET area/context/profile/credential/metrics chạy SNAPSHOT; SQL Server phải bật
ALLOW_SNAPSHOT_ISOLATION. Node+NodeRuntime+EvaluationState tạo trong một transaction;
thứ tự khóa ghi chuẩn `Session → Farm → Zone → Gateway → Node`. Các API trả `no-store`.
Không cần phần cứng để đăng ký hồ sơ hay cấp khóa.

Kiểm tra offline G1: từ `backend/` chạy `pytest tests/test_areas.py tests/test_devices.py tests/test_gateway_credentials.py tests/test_metrics.py`;
kiểm tra SQL Server thật (opt-in trên DB test disposable đã lên `fee0fa015af7`):
`$env:HQ_RUN_SQL_TESTS='1'; $env:HQ_EXPECT_TEST_DATABASE='HQTCSDL_test_ten'; pytest tests/test_sql_g1.py -v`.
Ghi chú: bật/tắt quyền nhận (`DEFERRED_FL04`) vẫn thuộc phạm vi G4.

## FL-06 — Ingestion, ACK2 và Gateway Simulator

Endpoint `POST /api/v1/ingestion/measurements` sử dụng xác thực `X-API-Key` (không dùng cookie/CSRF web), giới hạn body mặc định `HQ_INGESTION_MAX_BODY_BYTES=65536` (64 KiB, vượt trả `413 PAYLOAD_TOO_LARGE`), từ chối unknown fields, dùng Canonical-v1 và xử lý 1 transaction/record (`READ COMMITTED` + `SET XACT_ABORT ON`) theo thứ tự khóa `Farm (id ASC) -> Zone -> Gateway -> Node -> state`.

### Mẫu SQL có guard cho provisioning kỹ thuật `Node.gateway_id` (`G2-D04`)

Theo `FL-03`, Web UI không có chức năng ghép cặp `Node–Gateway`. Khi cần gán `Node.gateway_id` trên **DB test/local** (thay placeholder trước khi chạy, tuyệt đối không chạy nhầm DB đo thật):

```sql
-- Kiểm tra đúng DB đích trước khi ghi
IF DB_NAME() <> N'HQTCSDL_test_ten'
BEGIN
    THROW 50001, N'Sai database đích; dừng thao tác provisioning.', 1;
END;

DECLARE @device_code VARCHAR(128) = 'REPLACE_DEVICE_CODE';
DECLARE @gateway_code VARCHAR(128) = 'REPLACE_GATEWAY_CODE';

SET XACT_ABORT ON;
BEGIN TRANSACTION;

DECLARE @node_id BIGINT, @zone_id BIGINT, @zone_farm_id BIGINT, @gateway_id BIGINT, @gw_farm_id BIGINT;

SELECT @node_id = n.node_id, @zone_id = n.zone_id, @zone_farm_id = z.farm_id
FROM dbo.Node n
JOIN dbo.Zone z ON z.zone_id = n.zone_id
JOIN dbo.Farm f ON f.farm_id = z.farm_id
WHERE n.device_code = @device_code
  AND DATALENGTH(n.device_code) = DATALENGTH(CAST(@device_code AS VARCHAR(128)))
  AND z.archived_at IS NULL
  AND f.archived_at IS NULL;

SELECT @gateway_id = g.gateway_id, @gw_farm_id = g.farm_id
FROM dbo.Gateway g
JOIN dbo.Farm f ON f.farm_id = g.farm_id
WHERE g.gateway_code = @gateway_code
  AND DATALENGTH(g.gateway_code) = DATALENGTH(CAST(@gateway_code AS VARCHAR(128)))
  AND f.archived_at IS NULL;

IF @node_id IS NULL OR @gateway_id IS NULL OR @zone_farm_id <> @gw_farm_id
BEGIN
    ROLLBACK TRANSACTION;
    THROW 50002, N'Node/Gateway không tồn tại, đã lưu trữ hoặc không cùng Farm.', 1;
END;

UPDATE dbo.Node SET gateway_id = @gateway_id WHERE node_id = @node_id;
COMMIT TRANSACTION;
```

### Kiểm thử G2 và chạy Simulator

- Offline (từ `backend/`): `uv run pytest tests/test_ingestion_canonical.py tests/test_ingestion_api.py tests/test_gateway_simulator.py -v`
- SQL Server thật opt-in trên DB test disposable đã lên `fee0fa015af7` (từ `backend/`):
  ```powershell
  $env:HQ_RUN_SQL_TESTS = '1'
  $env:HQ_EXPECT_TEST_DATABASE = 'HQTCSDL_test_ten'
  uv run pytest tests/test_sql_g2.py -v
  Remove-Item Env:HQ_RUN_SQL_TESTS
  Remove-Item Env:HQ_EXPECT_TEST_DATABASE
  ```
- Gateway Simulator CLI: xem hướng dẫn chạy và giới hạn phần mềm tại [tools/README.md](../tools/README.md).

## FL-07 / FL-12 (G3) / FL-13 — Truy vấn, Dashboard dữ liệu và Báo cáo CSV

Nhóm G3 giữ nguyên Alembic head `fee0fa015af7` (không thêm migration hay bảng lưu báo cáo lâu dài) và giữ cố định thứ tự 5 đại lượng `temperature`, `airHumidity`, `pressure`, `light`, `soilMoisture` trên toàn bộ API/Web/CSV:

- **Biến môi trường G3 trong `backend/.env` (dùng placeholder khi khởi tạo, không commit secret thật):**
  - `HQ_REPORT_SECRET`: chuỗi ngẫu nhiên riêng, tối thiểu 32 byte UTF-8, **bắt buộc khác** `HQ_CSRF_SECRET` và `HQ_CURSOR_SECRET`; dùng để ký `snapshot_token` (`REPORT_V1`). Thiếu secret làm các endpoint tạo báo cáo / xuất CSV trả `503`.
  - `HQ_REPORT_SNAPSHOT_TTL_SECONDS`: số nguyên dương (mặc định `900`, tức 15 phút), kết hợp `min(ttl, session.expires_at)`.
  - `HQ_NODE_TIME_PROFILES`: JSON map theo `device_code` (`{"REPLACE_DEVICE_CODE":{"sampling_interval_seconds":60,"normal_delivery_budget_seconds":30}}`), mặc định `{}`. Node chưa khai báo profile có trạng thái freshness `PROFILE_MISSING` khi đang có số đo `AVAILABLE`.
- **Hợp đồng dữ liệu G3:**
  - `GET /api/v1/dashboard?farm_id=<id>[&node_id=<id>]` trả `capabilities: { monitoring: false, alerts: false }` và `null` cho các trường G4 (`active_count`, `monitoring`, `evaluation`, `active_alerts`).
  - `GET /api/v1/nodes/{id}/measurements/current|measurements|measurements/chart` dùng `view_token` + cursor HMAC (`HQ_CURSOR_SECRET`), tách luồng `known` (`t_node` hợp lệ) khỏi `unknown` (`t_node IS NULL`). Backend chart API đã sẵn sàng; phần render thư viện biểu đồ trên FE được hoãn sau G3/G4 (`G3-D05`).
  - `POST /api/v1/reports/measurements` tạo snapshot thống kê `SNAPSHOT` (loại trừ bản ghi `UNKNOWN`) và cấp `snapshot_token`; `POST /api/v1/reports/measurements/csv` xuất CSV 16 cột (UTF-8 BOM, CRLF, `QUOTE_ALL`, kèm lớp bảo vệ formula injection) theo `snapshot_token` trong `READ COMMITTED`.
  - Lưu ý nếu chạy backend/FE thủ công ở local khi đã cấu hình `HQ_REPORT_SECRET`: trình duyệt cần tải lại tab hoặc đăng nhập lại nếu vừa đổi secret.

### Kiểm thử offline và SQL Server cho G3

- Offline Backend (từ `backend/`):
  ```powershell
  uv run pytest tests/test_g3_config_time.py tests/test_measurements_api.py tests/test_dashboard_api.py tests/test_reports_api.py -v
  ```
- Offline Frontend (từ `frontend/`):
  ```powershell
  npm run typecheck
  npm test
  npm run build
  ```
- SQL Server thật opt-in trên DB test disposable đã lên `fee0fa015af7` (từ `backend/`):
  `test_sql_g3.py` đọc **duy nhất** `HQ_AUTH_TEST_DATABASE_URL` (không fallback sang `HQ_DATABASE_URL`) và đối chiếu tên DB với `HQ_EXPECT_TEST_DATABASE`:
  ```powershell
  $env:HQ_RUN_SQL_TESTS = '1'
  $env:HQ_EXPECT_TEST_DATABASE = 'HQTCSDL_test_ten'
  uv run pytest tests/test_sql_g3.py -v
  Remove-Item Env:HQ_RUN_SQL_TESTS
  Remove-Item Env:HQ_EXPECT_TEST_DATABASE
  ```
- Giới hạn kiểm chứng: bộ test offline và SQL Server thật không thay cho kiểm tra thủ công trên trình duyệt (điều hướng `/dashboard`, `/measurements`, `/reports`, filter, copy/phân trang, hết phiên) và mở file CSV UTF-8 BOM thực tế trên Excel / Google Sheets / LibreOffice Calc. Xem tổng hợp kết quả tại [g3-query-display-reports-validation.md](g3-query-display-reports-validation.md).
