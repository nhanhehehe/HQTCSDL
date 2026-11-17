# CSDL và migration

SQL Server/T-SQL là nền tảng chính. Alembic nằm tại `backend/migrations`; schema
nghiệp vụ được tạo bằng migration, không chạy `create_all()` khi API khởi động.
Seed dữ liệu nền tảng tách khỏi migration và fixture/simulator.

## Trạng thái hiện tại

M1 `a70b8056608d_create_m1_foundation_tables.py` đã merge qua
[PR #10](https://github.com/nhanhehehe/HQTCSDL/pull/10), tạo 8 bảng trong `dbo`:

| Nhóm | Bảng |
| --- | --- |
| Khu vực | Farm, Zone |
| Thiết bị | Gateway, Node, GatewayCredential |
| Auth | AdminAccount, AdminSession |
| Danh mục | MetricDefinition |

`alembic_version` là bảng theo dõi revision, không phải bảng nghiệp vụ thứ chín.
DB mới sau M1 có tổng 9 bảng. Source hiện có head `b15e2c03a001` (M2);
đọc phần M2 bên dưới trước khi chạy `upgrade head`. DB đã áp riêng M1 vẫn có
current `a70b8056608d`, không có nghĩa M2 đã được áp.

M2 có trong source; M3 (4 bảng cảnh báo/audit) chưa triển khai. FL-03 đã có API tạo
Node, thêm NodeRuntime/EvaluationState cùng transaction nên DB chỉ có M1 chưa đủ.
Migrations không tạo Admin, Gateway/Node demo hoặc khóa thiết bị. Seed nền tảng
chỉ thêm Farm/Zone/Metric; Admin tạo bằng [CLI riêng](../README.md#tao-tai-khoan-admin).
Backend không tự áp migration hoặc seed lúc startup.

<a id="ap-m1-va-seed"></a>

## Áp M1 và seed

### 1. Chuẩn bị và xác nhận đích

Làm [hướng dẫn SQL/môi trường](../docs/development.md) trước: DB test riêng, driver,
login/user và quyền. Alembic bắt buộc dùng `HQ_MIGRATION_DATABASE_URL`; không
fallback sang `HQ_DATABASE_URL`. `HQ_ENV` không tự chọn DB.

Chạy từ `backend/`, in DB/login thực tế của kết nối migration (không in URL):

```powershell
uv run python -c "from app.core.config import Settings; from sqlalchemy import create_engine, text; s=Settings(); e=create_engine(s.migration_database_url.get_secret_value(), hide_parameters=True); c=e.connect(); print(c.execute(text('SELECT DB_NAME(), SUSER_SNAME()')).one()); c.close(); e.dispose()"
```

Lần đầu kiểm chứng phải thấy tên DB test riêng và login migration. Câu SELECT này
chỉ đọc thông tin; nếu kết nối chưa đúng, sửa `.env` trước khi chạy migration.

### 2. Xem revision và SQL, rồi áp dụng

```powershell
uv run alembic heads
uv run alembic current
uv run alembic upgrade a70b8056608d --sql
uv run alembic upgrade a70b8056608d
uv run alembic current
```

- `heads`: head trong source; không kết nối DB.
- `current`: revision của DB đang được URL migration trỏ đến; DB mới có thể không có output.
- `--sql`: chỉ sinh SQL, chưa áp dụng. Đây là SQL từ base tới head, không tự đọc
  current của DB; dùng để review, không copy chạy lại vào DB đã có bảng.
- `upgrade a70b8056608d`: chỉ áp tới M1. Current sau đó là `a70b8056608d`.
  Lệnh upgrade có thể không in nhiều thông tin do scaffold chưa cấu hình logging.

Kiểm tra bằng SSMS trên đúng DB test (chọn dropdown hoặc chạy `USE [ten_db_test];`):

```sql
SELECT DB_NAME() AS database_name, SUSER_SNAME() AS login_name;

SELECT SCHEMA_NAME(schema_id) AS schema_name, name AS table_name
FROM sys.tables
WHERE is_ms_shipped = 0
ORDER BY name;

SELECT version_num FROM dbo.alembic_version;
```

Nếu migration lỗi, giữ thông báo để kiểm tra trạng thái/transaction; không dùng
`alembic stamp head` để bỏ qua lỗi hoặc tự xóa bảng nghiệp vụ để chạy lại.

### 3. Chạy seed riêng bằng SSMS

1. Mở `database/seed_foundation.sql` bằng **File → Open → File** trong SSMS.
2. Dùng kết nối migration và chọn đúng DB vừa áp M1. Nếu cần, thêm `USE [ten_db_test];`
   cùng `GO` trong cửa sổ chạy local; file trong repo không cố định DB của một người.
3. Chạy toàn bộ file (F5), gồm BEGIN/COMMIT; không chỉ chọn INSERT và bỏ COMMIT.
4. Chạy lại toàn bộ seed và kiểm tra kết quả dưới đây.

```sql
SELECT
    (SELECT COUNT(*) FROM dbo.Farm) AS farm_count,
    (SELECT COUNT(*) FROM dbo.Zone) AS zone_count,
    (SELECT COUNT(*) FROM dbo.MetricDefinition) AS metric_count;

SELECT f.farm_code, z.zone_code, z.name
FROM dbo.Zone AS z
JOIN dbo.Farm AS f ON f.farm_id = z.farm_id
ORDER BY z.zone_code;

SELECT metric_code, unit, min_value, max_value, decimal_places
FROM dbo.MetricDefinition
ORDER BY metric_code;
```

Trên DB mới: số lượng `1 / 2 / 5`; Farm `FARM-01`, Zone `INDOOR/OUTDOOR`.
Danh mục metric theo contract; soilMoisture dùng nhãn ngắn `% tương đối`, chỉ số
sau hiệu chuẩn Node, không phải phần trăm nước tuyệt đối.

Seed chỉ thêm mã thiếu, không ghi đè tên/đơn vị/miền đã có; nếu dữ liệu hiện hữu
khác dự kiến thì phải đối chiếu thay vì coi COUNT đúng là mọi giá trị đều đúng.
Zone seed thuộc Farm khác sẽ báo lỗi. Farm/Zone là dữ liệu khởi tạo local/demo,
không phải giới hạn schema chỉ một Farm hoặc hai Zone.

Chạy seed một đầu mối, không chạy đồng thời nhiều phiên. Script có transaction và
`XACT_ABORT ON`; chưa có runner tự động, chưa tự xác nhận DB đích. Không coi nó là
guard chống chạy nhầm DB. Không thêm mật khẩu, hash Admin chế tạo sẵn hoặc key thiết bị.
Nếu đã đủ Farm/Zone/5 metric theo profile thì không cần seed lại. Script dùng mã
FARM-01/INDOOR/OUTDOOR; profile dùng mã khác cần đối chiếu trước để tránh thêm khu
vực mẫu ngoài ý muốn. Admin đã có CLI riêng theo FL-01, không nằm trong seed.

### 4. Kiểm chứng downgrade trên DB test mới, trước khi giữ dữ liệu

Downgrade sau đây **xóa cả 8 bảng và dữ liệu M1**, gồm seed. Chỉ dùng trên DB test
có dữ liệu được phép bỏ; xác nhận DB/login bằng bước 1 trước khi chạy:

Phần này chỉ dành cho DB đang ở M1. Nếu DB đã ở M2, xem giới hạn downgrade M2 bên
dưới; không chạy `downgrade base` theo thói quen để thử lại migration.

```powershell
uv run alembic downgrade base
uv run alembic current
```

Sau downgrade, `current` không có revision; chỉ còn bảng `alembic_version` (không
có dòng version). Nếu chỉ kiểm M1, tạo lại bằng `uv run alembic upgrade a70b8056608d`,
kiểm current và chạy seed nếu thiếu dữ liệu nền. `upgrade head` sẽ áp cả M2, không
chỉ M1. Không dùng downgrade như cách vận hành thường xuyên trên dev/demo.

### 5. Áp lên DB dev sau kiểm chứng

Đổi DB đích trong URL migration sang DB dev, kiểm tra lại DB/login, chạy `current`
để biết trạng thái thực tế rồi `upgrade head` nếu còn thiếu. Mở seed trong SSMS,
chọn đúng DB dev và chạy nếu cần khởi tạo. Không suy ra DB dev đã có schema chỉ vì
DB test đã chạy thành công. Đặt lại cấu hình app/migration đúng mục đích sau thử;
khởi động lại Backend nếu thay `.env` của tiến trình đang chạy.

## Quy trình thay đổi schema tiếp theo

Thiết kế 03.4/luồng đã review → đối chiếu revision/DB thực → tạo revision có
`down_revision` đúng head → viết migration → kiểm SQL Server → PR vào develop.
Không sửa M1 đã áp dụng để thay đổi schema; thêm revision mới có review.
Nếu hai PR tạo hai head, điều phối dependency hoặc merge revision có review.

M1 hiện viết DDL tường minh; `Base.metadata` chưa có model nghiệp vụ đầy đủ.
Không dùng `--autogenerate` để suy ra/drop schema khi metadata còn trống.
Không có script reset/drop database tự động.

## Khi SSMS hiển thị cũ hoặc DROP TABLE bị chờ

- Object Explorer lưu danh sách cũ: **Tables → Refresh**; dùng SELECT sys.tables
  trên đúng server/DB/login để xác nhận. Hai login trên cùng DB thấy cùng schema
  trong phạm vi quyền được cấp, không có bản DB riêng của app/migration.
- Lỗi 1222 là hết thời gian chờ khóa. Một transaction thử còn mở có thể chặn DDL.
  Dùng `SELECT @@SPID, @@TRANCOUNT, DB_NAME();` ở từng tab thử, xác định đúng phiên;
  rollback transaction thử chưa commit trong chính tab đó. Hủy query không tự bảo
  đảm mọi transaction của phiên đã đóng. Không rollback công việc khác chưa xác định.
- Chỉ dọn bảng thử `dbo.FoundationProbe` trên DB test nếu còn tồn tại và đã hết
  transaction dùng bảng. Có thể dùng `SET LOCK_TIMEOUT 5000` cho phiên xóa để không
  chờ vô hạn; sau đó đặt `SET LOCK_TIMEOUT -1`. Không KILL hàng loạt hoặc restart
  SQL Server để thay việc xác định phiên giữ khóa.

Kết quả cụ thể và giới hạn kiểm chứng: [foundation-validation.md](../docs/foundation-validation.md).

## M2 — schema đo, cấu hình và runtime

Source có revision `b15e2c03a001`, nối tiếp M1 `a70b8056608d`. M2 thêm bảy bảng:
Measurement, MeasurementValue, NodeConfigVersion, ThresholdSetting, MonitoringPeriod,
NodeRuntime và EvaluationState; tổng 15 bảng nghiệp vụ. M1 và seed nền tảng giữ nguyên.

M2 đã sửa helper BIGINT để không tự suy diễn IDENTITY cho khóa dùng chung
`NodeRuntime.node_id` và `EvaluationState.node_id`. Chỉ ba khóa
`Measurement.measurement_id`, `NodeConfigVersion.config_id`,
`MonitoringPeriod.period_id` tự sinh ID. Test offline kiểm SQL tạo bảng thực tế.
Trước khi thử lại sau lỗi 544, xác nhận đúng DB, revision vẫn M1 và không còn
bảy bảng M2 sau rollback. Nếu trạng thái khác, đối chiếu trước khi chạy tiếp;
không dùng IDENTITY_INSERT, stamp hoặc xóa DB để bỏ qua lỗi. DB đã ghi nhận M2
thành công cần kiểm schema và phương án sửa riêng: sửa file không cập nhật DB đó.

Trước khi áp, xác nhận DB_NAME()/SUSER_SNAME() bằng migration URL, kiểm `alembic current`
và dữ liệu cần bảo toàn. Không suy DB đã được áp từ việc source có revision. Từ `backend`:

```powershell
uv run alembic heads
uv run alembic upgrade a70b8056608d:b15e2c03a001 --sql
# Chỉ chạy lệnh sau khi đã chọn và cho phép thay đổi đúng DB:
uv run alembic upgrade head
```

M2 thêm runtime/state rỗng cho Node M1 đã có: generation 0/0, ba checkpoint NULL,
không tạo config, period hay ngưỡng mặc định. API đăng ký Node trong
[devices](../backend/app/modules/devices/service.py) hiện thêm đúng một NodeRuntime
và EvaluationState trong cùng transaction; chỉ trả thành công sau commit.
Đúng năm Value/THRESHOLD setting, config đúng loại, pointer tới config hiện hành/period
đang mở và khoảng lịch sử không chồng cần validation trong đường ghi nghiệp vụ sau này.
CHECK/FK không tự bảo đảm các aggregate đó. FK ghép hiện bảo đảm pointer cùng Node.
Không có FK đến M3/AlertEpisode khi bảng chưa tồn tại; codec checkpoint chưa được thực thi.

Chỉ thử downgrade trên DB test được phép bỏ dữ liệu M2, về `a70b8056608d`, rồi upgrade
lại; không downgrade base hay reset seed. Downgrade M2 xóa bảy bảng mới và dữ liệu của
chúng, giữ tám bảng M1. Empty DB: áp M1 rồi M2; existing M1: M2 tái sử dụng hồ sơ hiện có.
Fixture SQL có opt-in và rollback được mô tả ở [hướng dẫn phát triển](../docs/development.md).
