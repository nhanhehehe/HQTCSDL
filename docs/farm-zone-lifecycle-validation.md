# PR-A — kiểm chứng migration Farm/Zone

Ngày kiểm: 18/11/2026. Phạm vi [issue #15](https://github.com/nhanhehehe/HQTCSDL/issues/15),
baseline `a846ac1d21d2adeef0a9f6a31553b25b7c2fa869`; source và `origin/develop`
khớp sau fetch. Nhánh `feat/15-farm-zone-lifecycle-schema` đạt branch policy.
Thiết kế: [FL-02](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14680393)
v5, [03.4](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14352390) v27.

## Revision và SQL offline

Alembic tạo revision `fee0fa015af7`, parent `b15e2c03a001`; một head duy nhất.
[Migration](../backend/migrations/versions/fee0fa015af7_add_farm_zone_lifecycle_columns.py)
chỉ thêm `archived_at DATETIME2(3) NULL`, `row_ver ROWVERSION NOT NULL` vào mỗi
bảng `dbo.Farm`, `dbo.Zone`. SQL range `b15e2c03a001:head --sql` đã review: bốn
ADD COLUMN và cập nhật `alembic_version` trong transaction, không DML nghiệp vụ
hay thay đổi bảng khác. Downgrade offline chỉ DROP bốn cột mới theo thứ tự ngược.

## SQL Server thật

Kiểm qua chính `HQ_MIGRATION_DATABASE_URL`: DB `HQTCSDL_test_nhan`, login
`hqtcsdl_migration_nhan`; quyền ALTER schema có hiệu lực. Current trước upgrade
`b15e2c03a001`; Farm/Zone chưa có cột lifecycle hoặc rowversion khác.
Sau upgrade Alembic current là `fee0fa015af7 (head)`.

Snapshot trước/sau dùng catalog metadata, số hàng và SHA-256 của toàn bộ cột cũ
trên cả 15 bảng nghiệp vụ; raw rows, password hashes, session/credential values,
DB URL và secret không được lưu/in. Digest và metadata local nằm trong cache
bị Git ignore; bảng sau chỉ ghi số hàng. Dữ liệu trong DB được giữ nguyên.

| Bảng | Trước | Sau |
| --- | --- | --- |
| Farm | 1 | 1 |
| Zone | 2 | 2 |
| Gateway | 1 | 1 |
| Node | 6 | 6 |
| AdminAccount | 1 | 1 |
| AdminSession | 4 | 4 |
| MetricDefinition | 5 | 5 |
| NodeRuntime | 6 | 6 |
| EvaluationState | 6 | 6 |
| GatewayCredential, Measurement, MeasurementValue, NodeConfigVersion, ThresholdSetting, MonitoringPeriod | 0 mỗi bảng | 0 mỗi bảng |

Đối chiếu PASS: tập bảng, metadata tất cả cột cũ (kiểu, nullability, collation,
identity, default), index/PK/UQ, toàn bộ FK (cột, target, trạng thái trust/enable,
delete/update action), CHECK và digest dữ liệu không đổi. Vì bao gồm toàn bộ
cột cũ, phép đối chiếu bảo vệ IDs, codes, metadata và liên kết cha/con.

| Bảng/cột | Kiểu catalog | max_length | scale | nullable | default |
| --- | --- | --- | --- | --- | --- |
| Farm.archived_at | datetime2 | 7 | 3 | Có | Không |
| Farm.row_ver | timestamp | 8 | 0 | Không | Không |
| Zone.archived_at | datetime2 | 7 | 3 | Có | Không |
| Zone.row_ver | timestamp | 8 | 0 | Không | Không |

Mọi Farm/Zone hiện hữu có archive NULL và `DATALENGTH(row_ver)=8`: PASS.
Không UPDATE fixture hay hàng thật để thử version. Chỉ kết nối ghi DB test đã
xác minh; không áp lên dev, không seed/stamp/reset/downgrade.

## Lệnh và kết quả

Môi trường: Windows, Python 3.12.16, SQL Server Express qua ODBC Driver 18.
`uv sync --locked --no-cache --python .venv\Scripts\python.exe`: PASS, 36 packages
đã có đủ; không thay lockfile/dependency. Các module dưới đây chạy bằng
`.venv\Scripts\python.exe -m` từ `backend`:

| Lệnh/module | Kết quả |
| --- | --- |
| `pytest tests/test_farm_zone_lifecycle_migration.py -v` | 5 passed |
| `ruff check .` | PASS |
| `ruff format --check .` | PASS, 35 files formatted |
| `pytest` | 81 passed, 2 skipped; 1 cảnh báo Starlette/httpx đã có |
| `alembic heads` | Một head `fee0fa015af7` |
| `alembic upgrade b15e2c03a001:head --sql` | PASS, review phạm vi như trên |
| Alembic `current`, `upgrade fee0fa015af7`, `current` qua Python API | PASS trên DB test; hậu kiểm snapshot PASS |
| `npm test` từ frontend (Node 24.22.0) | 16 passed |
| `npm run typecheck` | PASS |
| `npm run build` | PASS |
| `git diff --check` | PASS |

Lần đầu Ruff phát hiện định dạng migration mới; đã sửa và chạy lại đạt.
Sandbox chặn SQL instance và cache uv, TestClient bị treo; SQL/uv/pytest đã chạy
thành công ngoài sandbox. Không coi lần pytest bị ngắt trong sandbox là PASS.

## Chưa kiểm chứng

- Hai SQL auth/M2 opt-in tests vẫn SKIPPED; không bật fixture ghi ngoài phạm vi.
- Chưa chạy downgrade/re-upgrade trên DB disposable, fault/rollback migration,
  thử UPDATE để chứng minh rowversion tăng, concurrency hai kết nối hoặc browser.
- Chưa nghiệm thu Farm/Zone API/FE, chuyển đổi manifest, thiết bị/credential,
  ingestion hoặc worker; chúng thuộc các PR tiếp theo.
- Kết quả local không thay CI/review/merge. PR-B chỉ bắt đầu sau PR-A review/merge.
