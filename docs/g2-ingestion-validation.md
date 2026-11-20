# Báo cáo kiểm chứng G2 — Ingestion (`FL-06`) và Gateway Simulator

Ngày ghi nhận: **2026-11-19** | Nhánh: `feat/18-ingestion-sync` (từ `develop` `58959f2`) | Alembic head: `fee0fa015af7` (không tạo migration mới).

Tài liệu đối chiếu: `03.5` v28, `FL-06` v13, `03.5.1` v18, `03.1` v14, `03.4` v28, `03.6` v19, `02.1` v18, `02.2` v19, `02.4` v22, `04` v3 và 6 quyết định `G2-D01`–`G2-D05`.

---

## 1. Kiểm thử tự động Offline (Backend & Frontend)

| Kiểm tra | Lệnh | Kết quả thực tế |
| --- | --- | --- |
| Backend lint & format | `uv run ruff check . ../tools` và `uv run ruff format --check . ../tools` | **Đạt** (`All checks passed!`, 63 files formatted) |
| Alembic head | `uv run alembic heads` | **Đạt** (`fee0fa015af7 (head)`) |
| Backend unit & API offline | `uv run pytest` (từ `backend/`) | **Đạt** (`107 passed, 6 skipped`; 6 ca skipped là bộ SQL opt-in `test_sql_auth_m2.py`, `test_sql_g1.py`, `test_sql_g2.py`) |
| Frontend regression | `npm run typecheck`, `npm test`, `npm run build` (từ `frontend/`) | **Đạt** (`tsc --noEmit` OK, `19 passed`, `vite build` OK) |

Các nhóm ca đã kiểm trong offline suite:
- `test_ingestion_canonical.py` (13 ca): `C01–C10`, vector chuẩn `HQTCSDL/measurement/v1\n` khớp SHA-256 `794467f9ca01b4fad6a65273b3128b2b1d0b96cde3ba9aa6c1bf4d00365a9537`, `G2-D02a` strict extra fields, exact scale `2/2/2/1/1` (`28.567` reject, `28.500` accept, `-0.0 -> 0`), `MAX_INT64`, `D06` biên `+120`/`+121`.
- `test_ingestion_api.py` (5 ca tổng hợp): `D04-TC01–10`, `G2-D01` (`413 PAYLOAD_TOO_LARGE` vượt 65536 bytes), `G2-D02b` (`401 AUTHENTICATION_FAILED` / `403 GATEWAY_FORBIDDEN`, giữ nguyên G1 `credentials.py`), `G2-D03` thứ tự `D06 → Configuration → Receiving permissions`, mixed batch partial ACK, `E06-01` thiếu `NodeRuntime` rollback, `E06-03` revoke giữa batch dừng 403, `UQ 2601/2627` rollback + re-read, deadlock `1205` retry ≤3 lần, `UNKNOWN/null` không set `full_replay_required`.
- `test_gateway_simulator.py` (3 ca tổng hợp): `E06-02` kiểm tra toàn bộ `results[]` (trùng index, thiếu index, sai identity, mâu thuẫn `status`/`ack`), hàng đợi bền vững qua restart (`F20-AC10/13`), fairness `3:1` (`F20-AC23`), mất phản hồi + `413` chia batch + `429` `Retry-After` (`F20-AC08/22`).

---

## 2. Kiểm thử SQL Server thật Opt-in (`HQTCSDL_test_nhan`)

- **Môi trường:** Windows, Python `3.12.16`, SQL Server Express local (`HQTCSDL_test_nhan`, login ứng dụng tách quyền, Alembic revision đã xác minh `fee0fa015af7`, tuyệt đối không ghi DB `dev`/`demo`).
- **Lệnh:**
  ```powershell
  $env:HQ_RUN_SQL_TESTS='1'; $env:HQ_EXPECT_TEST_DATABASE='HQTCSDL_test_nhan'
  uv --directory backend run pytest tests/test_sql_g2.py -v
  ```
- **Kết quả thực tế:** **Đạt (`2 passed in 1.21s`, clean teardown)**:
  - `test_sql_g2_happy_path_metadata_runtime_rollback_and_cross_farm`: `VALID`, late `VALID` (`replay_from_t_node` giữ `MIN`), `UNKNOWN/null`, duplicate khác `recv_time`/`rssi` (`ALREADY_EXISTS`, không đổi `L`/`desired_generation`), `F13-AC07` cập nhật `last_new_persisted_at` trên `DeviceService.detail()` không đổi `row_ver`, `E06-01` thiếu `EvaluationState` rollback sạch 0 dòng mồ côi, `F03-AC11` cross-farm trả `CONFIGURATION_INVALID` với khóa Farm tăng dần.
  - `test_sql_g2_two_connection_races`: 5 kịch bản đồng thời 2 kết nối độc lập (`threading.Barrier(2)`): concurrent duplicate (`F19-AC10`), concurrent conflict (`F19-AC11`), key rotation & revoke đấu ingestion batch (`F13-KAC03/06`, `E06-03`), tắt `Node.receiving_enabled` đấu bản mới & duplicate (`F19-AC13/14`), archive `Zone` đấu ingestion — không deadlock, không mất/trùng dữ liệu.

---

## 3. Phần chưa chạy / ngoài phạm vi G2 (giữ mở trung thực)

- **Phần cứng IoT (`FL-05`):** ESP32 thật, LoRa HMAC-SHA256 (`TC-26`), ACK1, Flash/SD thật, RTC thật và thử nghiệm offline 24 giờ (`F20-AC16`) **chưa kiểm chứng**; simulator chỉ chứng minh hợp đồng phần mềm HTTP/ACK2/queue.
- **Nhóm sau:** G3 (`FL-07`, `FL-12` data, `FL-13` report/CSV), G4 (`FL-04` web toggle quyền, `FL-08/09/10/11`, Migration M3, background monitoring worker), và `FL-14` backup/restore runbook + đo RPO/RTO/NFR.
