# Báo cáo kiểm chứng G4 — Giám sát (`FL-04`, `FL-08`), Engine/Worker (`FL-09`, `FL-10`), Cảnh báo (`FL-11`) & Dashboard G4 (`FL-12`)

Ngày ghi nhận: **2026-11-21** | Nhánh: `feat/27-g4-monitoring-alerts` | Alembic head: `e7c4b6a10d53` (nối sau M3 `d6b3a5f09c42`).

Tài liệu đối chiếu: [Issue #27](https://github.com/nhanhehehe/HQTCSDL/issues/27) (quyết định `G4-D01`–`G4-D13`), `FL-04`, `FL-08`, `FL-09`, `FL-10`, `FL-11`, `FL-12`, `03.4`, `03.5.1`, `03.6`, [ui_ux_design_spec.md](design/ui_ux_design_spec.md) và [database/README.md](../database/README.md#ap-m3).

---

## 1. Kiểm thử tự động Offline (Backend & Frontend)

Chạy lại sau chỉnh sửa UI cuối cùng (commit `11c5409` cho Frontend và `fdff8fd` cho Backend):

| Kiểm tra | Lệnh | Kết quả thực tế |
| --- | --- | --- |
| Backend lint & format | `uv --directory backend run ruff check .` và `uv --directory backend run ruff format --check .` | **Đạt** (`All checks passed!`, `103 files already formatted`) |
| Alembic head | `uv --directory backend run alembic heads` | **Đạt** (`e7c4b6a10d53 (head)`) |
| Backend unit & API offline | `uv --directory backend run pytest -q` | **Đạt** (`162 passed, 19 skipped`; 19 ca skipped là các bài SQL opt-in cần biến môi trường) |
| Frontend TypeScript | `npm --prefix frontend run typecheck` | **Đạt** (`tsc --noEmit` exit `0`) |
| Frontend unit tests | `npm --prefix frontend test` | **Đạt** (`34 passed, 0 failed`, bổ sung `g4-views.test.mjs` so khớp `DECIMAL(9,2)`, `PROFILE_MISSING`, `422/409`, `AlertsStore`, `Dashboard` toàn Farm và mô tả `TIMEOUT`) |
| Frontend production build | `npm --prefix frontend run build` | **Đạt** (`vite build`, `84 modules transformed`) |

Phạm vi đã kiểm trong unit/API offline G4:
- **Schema & Migration (`test_m3_migration.py`):** kiểm tra render SQL từ `fee0fa015af7` → `d6b3a5f09c42` → `e7c4b6a10d53`, vòng FK `AlertEpisode.current_revision_no`, các filtered unique index cho episode `OPEN`, và nới `CK_ThresholdSetting_range` lên `DECIMAL(9,2)` trong khi giữ nguyên `MeasurementValue`/`MetricDefinition` ở `2/2/2/1/1`.
- **Cấu hình & Quyền tiếp nhận (`test_timeout_engine.py`, `test_monitoring_config_api.py`):** validate chuỗi thập phân chính xác `G4-D07`, lỗi `422 THRESHOLD_DECIMAL_INVALID` (`details.reason`, `details.field`), khóa đổi `T` khi `PROFILE_MISSING` trong khi vẫn cho phép sửa ngưỡng, cập nhật riêng/nguyên tử kèm `409 CONFIG_VERSION_CONFLICT`, tắt/bật tiếp nhận Node & Gateway fan-out (`409 VERSION_CONFLICT`/`AREA_ARCHIVED`).
- **Engine ngưỡng & Replay (`test_threshold_engine.py`):** `threshold_codec.py`, biên `PERIOD_END -> CONFIG -> PERIOD_START -> SAMPLE` tại cùng mốc `C`, xử lý `OPEN -> tắt ngưỡng -> replay` giữ revision đóng không ghi trùng, đóng `CONFIG_CHANGED` khi đổi chiều / hết vi phạm từ config mới mà không mở episode từ mẫu cũ, `AlertReplacement` (`SPLIT`, `MERGE`, `REWRITE`, `REPLACED`) và giữ ID khi có shared bad IDs + cùng chiều.
- **Worker & Scheduler (`test_alert_worker.py`):** hợp nhất trạng thái `UP_TO_DATE`/`PENDING`/`ERROR` giữa hai engine, dời cờ hoàn tất `completed_generation` / `full_replay_required` / `replay_from_t_node` chỉ khi cả hai engine xong và `desired_generation` còn khớp, retry timeout độc lập, trạng thái `/health/live` theo `G4-D10` (`disabled|starting|running|lock_unavailable|schema_missing|dead`, giữ `shutting_down` làm cờ nội bộ readiness).
- **API FL-11 & Dashboard G4 (`test_alerts_api.py`, `test_dashboard_api.py`):** bắt buộc `farm_id` (`G4-D02`), chặn `view=active` + `lifecycle=CLOSED` và `kind=TIMEOUT` + `recovery_proven`, từ chối tham số lạ ngoài đặc tả, global lookup `GET /alerts/{id}` cho cả episode retired, kiểm `acknowledged_revision_no` thuộc episode + khớp `acknowledgement_version` trước khi lookup bản ack cũ, lazy audit 3 tab + exact `acknowledgement_version`, `POST /alerts/{id}/acknowledgements` kèm `409 ALERT_VERSION_CONFLICT`/`EPISODE_RETIRED`, và `GET /api/v1/dashboard` bật `capabilities: { monitoring: true, alerts: true }`.
- **Frontend (`frontend/tests/g4-views.test.mjs`):** kiểm serialization chuỗi số thập phân không ép kiểu float, validation `PROFILE_MISSING`, `AlertsStore` `AbortController`, tổng hợp cảnh báo toàn Farm độc lập với Node đang chọn, và tiêu đề `TIMEOUT · Quá hạn lưu bản đo mới`.

---

## 2. Kiểm thử SQL Server thật Opt-in (`HQTCSDL_test_nhan`)

- **Môi trường:** Windows, Python `3.12.16`, SQL Server 2022 Express local (`HQTCSDL_test_nhan`, đọc duy nhất `HQ_AUTH_TEST_DATABASE_URL`, xác nhận `DB_NAME() == 'HQTCSDL_test_nhan'` và `alembic_version == 'e7c4b6a10d53'`).
- **Migration M3 & `e7c4b6a10d53` (đã kiểm trên DB test):**
  - `d6b3a5f09c42` vượt qua upgrade, kiểm catalog, 8 kiểm tra constraint/FK/rollback và vòng downgrade/re-upgrade (`d6b3a5f09c42 -> fee0fa015af7 -> d6b3a5f09c42`) bảo toàn đủ 15 bảng nền và checkpoint M2.
  - Upgrade lên `e7c4b6a10d53` thành công không mất dữ liệu cũ.
- **Bộ test SQL Server mới nhất (chạy lại sau Step 6 tại `11c5409`):**
  ```powershell
  $env:HQ_RUN_SQL_TESTS = '1'
  $env:HQ_EXPECT_TEST_DATABASE = 'HQTCSDL_test_nhan'
  uv --directory backend run pytest tests/test_sql_g4_monitoring.py tests/test_sql_g4_replay.py tests/test_sql_g4_worker.py tests/test_sql_g4_api.py tests/test_sql_g3.py -v -s
  Remove-Item Env:HQ_RUN_SQL_TESTS
  Remove-Item Env:HQ_EXPECT_TEST_DATABASE
  ```
  - **Kết quả thực tế:** **13 passed in 5.87s** (từng test tự dọn sạch dữ liệu fixture `testg4...` / `g4w-...` / `testg3-...` theo FK):
    - `test_sql_g4_monitoring.py` (**3 passed**): lưu ngưỡng 2 chữ số thập phân & rollback nguyên tử; luồng tắt nhận -> archive -> restore -> bật nhận + race hai kết nối; Gateway fan-out rollback & đối soát witness cùng mili-giây.
    - `test_sql_g4_replay.py` (**3 passed**): tương đương kết quả giữa đến đúng thứ tự vs đến trễ và `OPEN -> disable -> replay` không tạo revision trùng; rollback khi chèn lỗi + retry idempotent; replay song song với ingestion thật.
    - `test_sql_g4_worker.py` (**2 passed**): phục hồi sau restart worker, cô lập lỗi theo từng Node (**thời lượng 1 tick đo được: `154.73 ms` với `nodes=13`**); tranh chấp `sp_getapplock` hai kết nối & worker dự phòng tiếp quản sau khi kết nối giữ khóa đóng (`driver_connection.close()` với `pyodbc.pooling = False`).
    - `test_sql_g4_api.py` (**1 passed**): tổng hợp `GET /alerts`, `GET /dashboard` snapshot `FARM`, race xác nhận cảnh báo đồng thời hai kết nối (`409 ALERT_VERSION_CONFLICT`) và thu hồi phiên đăng nhập (`401`).
    - `test_sql_g3.py` (**4 passed**): xác nhận không hồi quy G3 trên schema `e7c4b6a10d53`.

---

## 3. Smoke Test trình duyệt thật trên Edge (Production build + Backend + Worker + SQL Server)

Ngày **2026-11-21**, kiểm tra thủ công trên **Microsoft Edge** (cửa sổ đang mở, zoom **67%**) kết nối Backend thật phục vụ production build tại cổng `8017` dùng `HQTCSDL_test_nhan`, worker G4 bật (`HQ_MONITORING_ENABLED=true`, `/health/live` trả `200` + `monitoring: "running"`):

- **Luồng đã xác nhận hoạt động qua UI:**
  - Gateway Simulator gửi bản đo nhận HTTP `200`, 1 bản ghi release và hàng đợi về `0`.
  - `/dashboard`: hiển thị đủ 5 đại lượng, cảnh báo ngưỡng `HIGH` toàn Farm và trạng thái `UP_TO_DATE`.
  - `/monitoring`: lưu cấu hình timeout `T=1` và ngưỡng `light` chính xác `0.05..500.55`.
  - `/devices` & `/monitoring`: bật/tắt quyền tiếp nhận Node và Gateway (gồm hộp thoại xác nhận fan-out); timeout chuyển `ACTIVE`/`PAUSED` khớp thao tác và mở episode `TIMEOUT` sau khi quá hạn.
  - `/alerts` & `/alerts/:id`: tải danh sách `active`/`history` (cửa sổ 24h), mở deep-link chi tiết, thực hiện acknowledgement thành công khi episode vẫn `OPEN`, tab audit `acknowledgements` hiển thị bản ghi vừa lưu; banner cross-Farm giữ nguyên Farm trên Topbar cho tới khi người dùng bấm nút chuyển.
  - Đăng xuất quay về màn hình đăng nhập.
- **Phát hiện trong smoke test và khắc phục ngay tại `11c5409`:**
  1. Nhãn `TIMEOUT` ở [alerts/store.ts](../frontend/src/features/alerts/store.ts#L280) trước đây ghi *"Mất kết nối / quá hạn dữ liệu"* — đã sửa thành `TIMEOUT · Quá hạn lưu bản đo mới`.
  2. Mô tả tắt quyền tiếp nhận Node/Gateway ở [DevicesPanel.tsx](../frontend/src/features/devices/DevicesPanel.tsx#L955) trước đây nói đóng mọi episode OPEN — đã sửa đúng ngữ nghĩa *khóa nhận mẫu mới, đóng timeout period đang mở (nếu có) và tạm dừng giám sát; episode ngưỡng OPEN được giữ nguyên khi pause*.
  - Sau khi sửa đã chạy lại `typecheck`, `test` (`34 passed`), `vite build` và kiểm tra đối chiếu offline (`162 passed`), ruff đạt.
- **Fixture giữ lại trên `HQTCSDL_test_nhan`:** Farm IDs `82`/`83` (`EDGE G4 Smoke` / `EDGE G4 Other Farm`), Node `119` (`EDGE-G4-SMOKE-N1`), Gateway `67` (evaluation đạt `8/8` không lỗi; profile test `10s/5s`); API key giả đã thu hồi, hàng đợi simulator đã dọn và server test đã tắt.

---

## 4. Hạng mục chưa chạy / Giữ mở trung thực (chờ nghiệm thu riêng hoặc ngoài phạm vi G4)

1. **2 ca Migration M3 còn mở trong kiểm thử SQL Server thật:**
   - Bootstrap Alembic từ đầu trên một DB hoàn toàn trống tới `e7c4b6a10d53`.
   - Kiểm chứng rollback DDL tự động trên SQL Server khi chèn lỗi giữa chừng migration `d6b3a5f09c42`.
2. **Mất kết nối DB từ phía SQL Server (`KILL <spid>` thật):**
   - `test_sql_worker_two_connection_applock_takeover_and_session_loss` xác minh worker 2 giành lại `sp_getapplock` sau khi ngắt kết nối socket phía Python (`driver_connection.close()` với `pyodbc.pooling = False`); chưa thực hiện lệnh T-SQL `KILL <spid>` trực tiếp vì login test local không có quyền server-level `ALTER ANY CONNECTION`.
3. **Các ca UI/Trình duyệt chưa phủ trong đợt Edge smoke vừa rồi:**
   - Ma trận 6 tổ hợp trình duyệt × độ phân giải (`NFR-08`) — mới quan sát Edge ở một kích thước cửa sổ tại zoom 67% và chưa mở lại Edge sau commit sửa wording `11c5409`.
   - Tiêm lỗi mạng `503` / `401` / reconnect trong khi đang mở UI.
   - Quyền thông báo hệ điều hành (`Notification`), nút đối soát exact `acknowledgement_version` khi mất phản hồi `POST /acknowledgements`, hiển thị lineage `SPLIT`/`MERGE`/`REWRITE` qua UI thật, và hai tab audit `revisions` & `replacements` trên trình duyệt.
4. **Ngoài phạm vi G4 (theo kế hoạch chung):**
   - Vẽ biểu đồ Frontend (`G3-D05`), benchmark hiệu năng định lượng `NFR-04`/`NFR-05`/`NFR-08`, phần cứng ESP32/LoRa (`FL-05`), và runbook sao lưu/phục hồi vận hành (`FL-14`).
