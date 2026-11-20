# Báo cáo kiểm chứng G3 — Truy vấn (`FL-07`), Dashboard dữ liệu (`FL-12` G3) và Báo cáo CSV (`FL-13`)

Ngày ghi nhận: **2026-11-20** | Nhánh: `feat/25-g3-query-display-reports` (từ `develop` `4a33475`) | Alembic head: `fee0fa015af7` (không tạo migration mới, không thêm bảng lưu báo cáo lâu dài).

Tài liệu đối chiếu: `03.5` v28, `FL-07`, `FL-12`, `FL-13`, `03.5.1`, `03.4` v28, `03.6`, `02.4`, `02.5`, `04`, [ui_ux_design_spec.md](design/ui_ux_design_spec.md) (§4.2, §4.5, §4.8, §5, §8–§9) và 6 quyết định `G3-D01`–`G3-D06`.

---

## 1. Kiểm thử tự động Offline (Backend & Frontend)

| Kiểm tra | Lệnh | Kết quả thực tế |
| --- | --- | --- |
| Backend lint & format | `uv --directory backend run ruff check .` và `uv --directory backend run ruff format --check .` | **Đạt** (`All checks passed!`, 77 files formatted) |
| Alembic head | `uv --directory backend run alembic heads` | **Đạt** (`fee0fa015af7 (head)`) |
| Backend unit & API offline | `uv --directory backend run pytest -v` | **Đạt** (`123 passed, 10 skipped`; 10 ca skipped là bộ SQL opt-in `test_sql_auth_m2.py`, `test_sql_g1.py`, `test_sql_g2.py`, `test_sql_g3.py`) |
| Frontend regression | `npm --prefix frontend run typecheck`, `npm --prefix frontend test`, `npm --prefix frontend run build` | **Đạt** (`tsc --noEmit` OK, `28 passed, 0 failed`, `vite build` 78 modules OK) |

Các nhóm ca đã kiểm trong offline suite G3:
- `test_g3_config_time.py` (8 ca): `G3-D04` (`HQ_REPORT_SECRET` >= 32 byte UTF-8, bắt buộc khác `HQ_CSRF_SECRET` và `HQ_CURSOR_SECRET`, `HQ_REPORT_SNAPSHOT_TTL_SECONDS > 0`, `HQ_NODE_TIME_PROFILES` từ chối duplicate JSON keys / `NaN` / `Infinity` / mã thiết bị không hợp lệ), `parse_rfc3339_utc` & `resolve_event_range` (`ceil(from)` / `floor(to)` chính xác trên `Decimal`, khoảng lẻ giây không chứa giây nguyên hợp lệ), định dạng `UTC+07:00`.
- `test_measurements_api.py` (6 ca tổng hợp): `FL-07` (`F06-AC01..19`, `E07-01..04`) — `current` luôn lấy bản có `t_node` mới nhất kèm đủ 5 đại lượng theo thứ tự cố định (`temperature`, `airHumidity`, `pressure`, `light`, `soilMoisture`) kể cả khi bản mới toàn lỗi hoặc có bản trễ `09:30` đến sau `10:05`; giữ số `0` hợp lệ; `known` (`DESC`) và `chart` (`ASC`) dùng chung `view_token` + keyset `(t_node, boot_id, seq)`; `unknown` (`t_node IS NULL`) tách biệt và phân trang theo `(persisted_at, measurement_id)`; đổi `HQ_DATASET_GENERATION` vô hiệu `view_token`/`cursor`; thiếu/sai `MeasurementValue` trả `503 STORAGE_UNAVAILABLE`; retry deadlock `1205` tối đa 3 lần khi chụp `cap`.
- `test_dashboard_api.py` (2 ca tổng hợp): `FL-12` phần dữ liệu & freshness (`G3-D01`, `G3-D02`, `E12-01..03/07`) — bắt buộc `farm_id`, tùy chọn `node_id` (`404` thiếu Farm/Node, `400` Node khác Farm, `200` cho Farm/Zone đã lưu trữ kèm `archived_at`), trả `capabilities: { monitoring: false, alerts: false }` và `null` cho toàn bộ trường G4 (`active_count`, `monitoring`, `evaluation`, `active_alerts`), kiểm đủ 6 trạng thái `FreshnessView` (`NO_DATA`, `NO_EVENT_TIME`, `CLOCK_AHEAD`, `PROFILE_MISSING`, `FRESH`, `STALE` tại biên `age == stale_after` và `age > stale_after`), không lộ `backend_received_at` / `last_new_persisted_at` / credential.
- `test_reports_api.py` (5 ca tổng hợp): `FL-13` (`F11-AC01..10`, `E13-01..08`) — strict input `{ node_id, from?, to? }` (`G3-D03`), làm tròn `ROUND_HALF_UP` tối đa 2 chữ số thập phân (`1.125 -> 1.13`, `-1.125 -> -1.13`, `-0.004 -> 0`), giữ số `0` hợp lệ và mẫu trùng giá trị, loại trừ bản ghi `UNKNOWN`, ký/xác minh `REPORT_V1` trên raw bytes trước khi parse JSON, kiểm ưu tiên `401 SESSION_INVALID` trước `410 SNAPSHOT_EXPIRED`, kiểm `session_id` (`400 TOKEN_INVALID`) và `dataset_generation`/`source` (`409 REPORT_CONTEXT_CHANGED`), xuất CSV 16 cột (`UTF-8 BOM`, `CRLF`, `QUOTE_ALL`) kèm bảo vệ OWASP formula injection (`text:` sau NFKC cho cột văn bản, giữ nguyên số âm), và tương thích đơn vị `soilMoisture` (`% tương đối` theo seed M1 & `%`).
- `frontend/tests/g3-views.test.mjs` & `shell-state.test.mjs`: chuyển `DEFAULT_AUTHENTICATED_ROUTE = '/dashboard'` (`G3-D06`), `DashboardCoordinator` single-flight polling 5000ms / timeout 4000ms / tạm dừng khi ẩn tab / giữ snapshot cũ khi lỗi `503` / xóa dữ liệu riêng khi `401`, `MeasurementsStore` tách `current` khỏi bộ lọc lịch sử và hủy response cũ khi đổi Node/mode, `ReportsStore` khóa xuất CSV ngay khi `draft` lệch `result`, không tự tạo lại report khi nhận `410`, và thống nhất 1 nút `Mặc định 24h` (để trống `Từ/Đến` để Backend chốt `[as_of - 24h, as_of]` theo giờ SQL Server).

---

## 2. Kiểm thử SQL Server thật Opt-in (`HQTCSDL_test_nhan`)

- **Môi trường:** Windows, Python `3.12.16`, SQL Server 2022 Express local (`HQTCSDL_test_nhan`, đọc duy nhất `HQ_AUTH_TEST_DATABASE_URL`, đối chiếu `DB_NAME() == HQ_EXPECT_TEST_DATABASE` và `alembic_version == 'fee0fa015af7'`).
- **Lệnh:**
  ```powershell
  $env:HQ_RUN_SQL_TESTS='1'; $env:HQ_EXPECT_TEST_DATABASE='HQTCSDL_test_nhan'
  uv --directory backend run pytest tests/test_sql_g3.py -v
  ```
- **Kết quả thực tế:** **Đạt (`4 passed in 1.67s`, dọn sạch dữ liệu `testg3-%` theo đúng thứ tự FK)**:
  1. `test_sql_g3_e2e_ingest_to_fl07_fl12_fl13_and_archived_farm`: Luồng tích hợp `IngestionService` → `MeasurementsService` (`current` giữ mẫu `t_node` mới nhất thay vì mẫu đến trễ, `known` `DESC` và `chart` `ASC` chia sẻ `view_token` cùng tập bản ghi, `unknown` phân trang keyset độc lập, thứ tự cố định 5 đại lượng) → `DashboardService` (`FARM`/`NODE` scope, `FRESH`/`STALE`/`NO_EVENT_TIME`, cross-farm `400`, Farm/Zone đã lưu trữ trả `200` kèm `archived_at`, các trường G4 đều `null`) → `ReportService` (tạo snapshot thống kê `SNAPSHOT` loại trừ `UNKNOWN`, giữ số `0` hợp lệ, xuất CSV 16 cột `UTF-8 BOM` + `CRLF` khớp từng dòng với Web và khớp đơn vị `% tương đối` từ `dbo.MetricDefinition`).
  2. `test_sql_g3_fl07_two_connection_cap_capture_race`: Hai kết nối SQL Server song song — Connection A giữ writer guard chuẩn (`Farm -> Zone -> Gateway -> Node` với `UPDLOCK, HOLDLOCK`) và chèn bản ghi `M2` chưa commit; Thread B gọi `list_measurements` phải đợi A commit mới chốt `cap >= M2.measurement_id`. Bản ghi `M3` chèn sau `cap` (dù có `t_node` nằm giữa `M2` và `M1` trong khoảng lọc) bị loại hoàn toàn khỏi các trang tiếp theo và khỏi `/chart` dùng cùng `view_token` (`E07-03`).
  3. `test_sql_g3_fl13_snapshot_isolation_and_csv_stability`: Sau khi tạo `report1`, nạp thêm bản ghi mới trong khoảng lọc và đổi tên Node (`DeviceService.patch`); xuất CSV từ `snapshot_token` cũ vẫn giữ nguyên `total_records`, các thống kê `min/max/avg` và nhãn cũ; tạo `report2` mới phản ánh bản ghi mới và tên Node mới (`E13-04`).
  4. `test_sql_g3_fl13_logout_blocks_csv_export`: Thu hồi phiên qua `AuthService.logout` làm `ReportService.export_csv` bị chặn ngay bằng `401 SESSION_INVALID` (`E13-08`).

---

## 3. Kiểm thử Smoke Test thủ công trên Trình duyệt & Xuất CSV

Người vận hành đã thực hiện và xác nhận hoàn thành các kịch bản smoke test trên trình duyệt cùng dữ liệu mẫu G3 trên `HQTCSDL_test_nhan` (`FARM-01`, `NODE-01` có profile `60s + 30s = 90s`, `NODE-02` không khai báo profile, `NODE-03` chỉ có bản ghi `UNKNOWN`, các Node `2..6` chưa có dữ liệu):
- **Điều hướng & Chọn ngữ cảnh:** Đăng nhập tự động vào `/dashboard`; chọn Farm tường minh trên Topbar dùng chung xuyên suốt `/dashboard`, `/measurements`, `/reports`.
- **Dashboard (`/dashboard` — `FL-12` G3):** Hiển thị đúng 5 trạng thái độ mới (`FRESH`, `STALE` tự chuyển sau 90 giây qua nhịp poll 5 giây, `PROFILE_MISSING`, `NO_EVENT_TIME`, `NO_DATA`), chọn thẻ Node xem đủ 5 đại lượng theo thứ tự cố định, giữ banner phạm vi G3 (`capabilities.monitoring = false, alerts = false`).
- **Dữ liệu đo (`/measurements` — `FL-07`):** Số đo hiện tại độc lập với bộ lọc lịch sử; bảng `Known` hiển thị đúng số `0` hợp lệ và badge lỗi `READ_ERROR` / `INVALID_VALUE`; tab `Biểu đồ` hiển thị thông báo hoãn FE chart (`G3-D05`); tab `Unknown` ẩn bộ lọc `Từ/Đến` và tách riêng bản ghi thiếu giờ đo; nút `Mặc định 24h` thống nhất để trống `Từ/Đến` lấy cửa sổ 24h theo giờ Server.
- **Báo cáo & CSV (`/reports` — `FL-13`):** Tạo snapshot thống kê `N_total = 6` (loại trừ 3 bản `UNKNOWN`), khóa nút `Xuất CSV` ngay khi sửa bộ lọc Draft và mở lại khi bấm `Mặc định 24h`, tải tệp CSV 16 cột (`UTF-8 BOM`) khớp hoàn toàn với bảng Web.

---

## 4. Phần chưa chạy / ngoài phạm vi G3 (giữ mở trung thực)

- **Biểu đồ trên Frontend (`G3-D05`):** Backend API `GET /api/v1/nodes/{id}/measurements/chart` + shared `view_token` đã hoàn tất và kiểm chứng; việc chọn thư viện biểu đồ FE và vẽ canvas/SVG được hoãn tới sau khi hoàn thành G3 và G4.
- **Nhóm G4 (`FL-04`, `FL-08/09/10/11`, `FL-12` phần cảnh báo):** Bật/tắt quyền nhận trên web (`FL-04`), cấu hình ngưỡng & timeout `T` (`FL-08`), Migration M3 (`AlertEpisode`, `AlertRevision`, `AlertReplacement`, `AlertAcknowledgement`), background worker đối soát ngưỡng/replay/timeout (`FL-09`, `FL-10`), màn hình quản lý/ghi nhận cảnh báo (`FL-11`) và phần cảnh báo trên Dashboard (`FL-12`).
- **Hiệu năng, Phần cứng & Vận hành:** Đo benchmark định lượng `NFR-04` (`P95 <= 3s`, >= 30 lần), `NFR-05` (`<= 10s`), ma trận 6 tổ hợp trình duyệt × độ phân giải (`NFR-08`), phần cứng ESP32/LoRa thật (`FL-05`) và runbook sao lưu/phục hồi `FL-14` (`NFR-03`).
