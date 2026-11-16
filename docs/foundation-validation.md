# Kiểm chứng nền tảng

## Lần dựng khung Linux/CI — 14/11/2026

Đối chiếu 03.3 Confluence v7 và 03.6 v2 trước khi dựng. develop nền:
`020146f451600c2f0ccc9a085c97c97053bf6f92`. Issue #5.

Môi trường chạy: Linux, Python 3.12.14, uv 0.13.6, Node 24.22.1, npm 11.9.0.
Phiên bản thư viện chính xác nằm trong uv.lock/package-lock.json.

| Kiểm tra đã chạy | Kết quả |
| --- | --- |
| `uv sync --locked` | Cài thành công từ lockfile |
| `uv run ruff check .` | Đạt |
| `uv run ruff format --check .` | Đạt |
| `uv run pytest` | 10 passed; có cảnh báo deprecation httpx từ Starlette TestClient |
| `uv run alembic heads` | Đạt; chưa có revision nên chưa tạo schema |
| `npm ci` | Cài thành công từ package-lock |
| `npm run typecheck` | Đạt |
| `npm run build` | Đạt; Vite build thành công |
| Smoke qua Uvicorn thật | Khởi động một process, `/health/live` 200; index và hai asset build 200; readiness thiếu SQL 503; API/asset thiếu 404 |

Test readiness success/failure dùng probe giả lập, **không chứng minh kết nối SQL**.
Môi trường Linux của lần dựng khung này không có ODBC driver đăng ký (`pyodbc.drivers() == []`) hoặc SQL Server.
Không có database, migration nghiệp vụ hoặc dữ liệu đo thật bị ghi/thay đổi.

Các mục theo dõi từ lần dựng khung (đối chiếu kết quả cập nhật bên dưới):

- Thành viên thứ hai làm theo README trên máy nhóm; ghi OS/runtime/SQL instance/driver.
- Kiểm chứng driver/SQL trên các máy chưa chạy. Máy Nhân đã có bằng chứng ngày 15/11 bên dưới.
- Khi migration đầu tiên đã review: áp dụng, kiểm tra ràng buộc, đọc/ghi và phục hồi trên DB test.
- Khi dựng worker: kiểm chứng một thread, dừng/restart, việc lưu bền vững và health khi worker chết.
- Kiểm tra CI GitHub, review PR và đặt required checks phù hợp. CI không thay approval.
- Kiểm thử AC/NFR từng luồng và phần cứng theo kế hoạch riêng.


## Kết quả máy Nhân — 15/11/2026, T01/T04/M1

Nguồn: output và xác nhận Nhân trực tiếp chạy trên Windows trong cuộc trò chuyện
hướng dẫn; nội dung kiểm tra cũng ghi ở [PR #10](https://github.com/nhanhehehe/HQTCSDL/pull/10)
đã merge vào develop (commit `a491d847bb75ec25ea3c21ac95ac46ccb891aec5`). Không mô tả
đây là AI đã truy cập DB trên máy Nhân hoặc có log/screenshot đính kèm. Bằng chứng
Linux/CI ở trên là một môi trường/đợt khác.

### G0-T01 — SQL/driver trực tiếp

| Nội dung | Kết quả Nhân cung cấp |
| --- | --- |
| SQL/OS | SQL Server 2022 RTM Express 64-bit, 16.0.1000.6; @@VERSION báo Windows 10 Home 10.0 X64 Build 26300, Hypervisor |
| Instance | `DESKTOP-1DDOF5K\SQLEXPRESS` |
| Mốc đọc cấu hình | `2026-11-15 06:03:04.6860034 UTC` (13:03:04.6860034 giờ Việt Nam) |
| Driver/Backend | Python nhận ODBC Driver 18; `/health/ready` xác nhận DB dev kết nối được. Chưa thu bản driver/pyodbc chi tiết của lần chạy đó |
| DB | `HQTCSDL_dev_nhan`, `HQTCSDL_test_nhan`: ONLINE, SQL_Latin1_General_CP1_CI_AS, AUTO_CLOSE=0, ALLOW_SNAPSHOT_ISOLATION=ON, READ_COMMITTED_SNAPSHOT=0 |
| Login/user | Hai login `hqtcsdl_app_nhan` / `hqtcsdl_migration_nhan`, user/quyền riêng trong mỗi DB; Nhân chọn không tạo thêm login test |
| Quyền app | DML được cấp; CREATE TABLE/ALTER schema=0; thực tế tạo bảng thử bị từ chối |
| Quyền migration | Tạo/sửa bảng trong dbo và DML; không cấp sysadmin/db_owner |
| Commit/rollback | Migration tạo FoundationProbe; app đọc/thêm/sửa/xóa. Rollback trả (1,100); commit insert có (2,300), commit delete xóa được dòng 2 |
| SNAPSHOT | A đọc 100; B cập nhật 200 và commit; A cùng transaction vẫn 100, sau commit đọc 200 |
| Dọn bảng | DROP có lần chờ/lỗi 1222; output thấy app còn transaction mở và migration có transaction lồng. Sau xử lý Nhân xác nhận dọn bảng và refresh SSMS; bảng thử không thuộc M1 |

Phần SQL trực tiếp của T01 đã kiểm chứng; không tự chứng minh transaction nghiệp vụ
qua Python, concurrency từng luồng hoặc NFR đã đạt.

### G0-T04 — phạm vi đơn giản đã thống nhất

- Code thực tế là bản Nhân tự làm trong PR #10: WebModel strict/extra=forbid,
  UTC clock millisecond, handler lỗi web/validation 400, tận dụng request ID cũ và
  giữ ingestion riêng. `no-store` hiện đặt trên lỗi web, chưa coi mọi success đã có.
- Ruff lint/format đã được Nhân xác nhận đạt; `uv run pytest -q`: **16 passed**
  (10 test nền + 6 test schema), còn cảnh báo deprecation httpx của Starlette TestClient.
- Postman GET API web không tồn tại trả 404 NOT_FOUND theo format mới và request_id.
- HTTP validation qua endpoint có schema, lỗi đặc thù/SQL handler, full ID/Decimal/
  rowversion codecs chưa nghiệm thu trong phạm vi đơn giản. Yêu cầu kiểu dữ liệu
  không bị bỏ: bổ sung đúng khi luồng cần dùng.
- [PR #9](https://github.com/nhanhehehe/HQTCSDL/pull/9) là bản tham khảo đã đóng,
  không merge; **78 test của PR đó không phải kết quả bản develop hiện tại**.

Khi đồng bộ hướng dẫn ngày 15/11/2026, AI đã chạy lại trên Linux/Python 3.12.14 từ
source develop sau PR #10: `uv sync --locked`, Ruff lint/format, pytest **16 passed**,
`alembic heads` đúng M1 và sinh SQL offline đủ 9 CREATE TABLE. Đã kiểm tra liên kết
nội bộ/anchor của các hướng dẫn. Kết nối offline dùng URL placeholder, không mở
kết nối DB và không thay bằng chứng SQL thật trên Windows ở mục T01/M1.

### G0-T02 — M1 và seed

| Kiểm chứng | Kết quả |
| --- | --- |
| Alembic credential | Đã bỏ fallback app URL; Python SELECT DB_NAME()/SUSER_SNAME() trả DB test và login migration đúng |
| Revision | `a70b8056608d`, 8 bảng nghiệp vụ + alembic_version |
| SQL offline | Đã xem 8 CREATE TABLE và PK/FK/UNIQUE/CHECK; sinh SQL chưa là chạy SQL thật |
| Áp DB test | Upgrade thành công trên `HQTCSDL_test_nhan`; Nhân xác nhận danh sách bảng/current đúng |
| Downgrade/upgrade | Downgrade base rồi upgrade lại thành công trên DB test được phép bỏ dữ liệu |
| Seed | File `database/seed_foundation.sql`, chạy bằng migration trong SSMS; 1 Farm, 2 Zone, 5 MetricDefinition |
| Chạy seed lại | Vẫn 1/2/5; đã đối chiếu output theo hướng dẫn |

M1/schema và seed đã hoàn thành, kiểm chứng trên DB test theo bằng chứng trên;
chưa tạo Admin, Gateway/Node hoặc key thật. Chưa có test tự động mọi ca vi phạm
FK/UQ/CHECK/rollback nghiệp vụ hoặc seed đồng thời. Không suy ra trạng thái DB dev
từ DB test: kiểm DB/login/current của dev trước khi áp dụng.

### Phần còn lại trước nghiệm thu sản phẩm

- Thành viên thứ hai thực hiện README trên máy riêng, ghi môi trường/kết quả mới.
- FL-01/T05: hash password/khởi tạo Admin, login/session/logout, CSRF/cookie,
  transaction/guard và SQL/HTTP; T06/browser kiểm riêng.
- M2/runtime/state/fixture và M3/audit/worker theo thiết kế; không coi M1 thay phần đó.
  Kiểm UQ/FK/locking/race/restart theo luồng trên SQL thật.
- AC/NFR/browser/hardware cần bằng chứng riêng; không suy từ 16 test hoặc readiness.

Hướng dẫn tái hiện: [SQL/cấu hình](development.md), [M1/seed](../database/README.md).

### T05/T06/M2 — bổ sung source ngày 16/11/2026

Nhánh triển khai `feat/3-auth-and-m2`, xuất phát từ HEAD `66de752`. Giữ nguyên
`.env`, migration M1 và `seed_foundation.sql`; không tạo Admin, chạy seed, upgrade
hay downgrade DB trong phiên triển khai này.

- T05: Argon2id m19456KiB/t2/p1; login/session/logout, cookie/CSRF, SQL session guard,
  recheck account và rotation/revoke cùng transaction, commit trước trả thành công.
  Lệnh tạo Admin dùng getpass và kiểm DB/login riêng, không gọi khi startup.
- T06: trạng thái auth/CSRF trong memory, loại response generation cũ, hết hạn tuyệt
  đối, xác minh khi focus/resume và che nội dung riêng khi logout chưa được xác nhận.
- M2: revision `b15e2c03a001`, bảy bảng, FK/index/CHECK và ba checkpoint; thêm state
  rỗng cho Node M1 đã có, không cấu hình/period/ngưỡng giả. Chưa có M3 hoặc worker.
- Kiểm tra: locked dependencies đồng bộ bằng uv local trong `.venv` với `--inexact`
  để giữ package cài thêm; không cài công cụ toàn cục. Backend 42 test đạt; hai ca
  SQL có opt-in chưa chạy. Frontend bốn test client đạt; typecheck/build đạt.
- SQL offline được biên dịch theo dialect MSSQL cho upgrade/downgrade M2; chưa là
  bằng chứng chạy DDL. Kết nối đọc thử runtime và test đều báo OperationalError
  trong môi trường agent; không suy trạng thái revision/Admin/data ở DB thật.
- Chưa kiểm browser thật, SQL locking/race hai kết nối, migration trên empty/existing
  M1 và rollback/upgrade thật. Unit tests dùng transaction double, không dùng SQLite
  để thay bằng chứng SQL Server. F01-AC01..14/E01-01..04 chưa nghiệm thu tích hợp.
- Benchmark tùy chọn đã đo 20 verify: Windows 11/Python 3.12.16/AMD64 Family 25
  Model 117, min 16.82ms, median 17.81ms, p95/max 35.99ms. Không dùng phép đo này
  để kết luận đạt NFR; theo yêu cầu người dùng, benchmark không chặn hoàn thành.
