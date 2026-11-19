# Ranh giới module

Theo 03.3: `auth`, `devices`, `ingestion`, `measurements`, `monitoring_config`,
`alerts`, `reports`. Chỉ tạo code module khi nhận task/thiết kế luồng đã review.

Hiện có `auth` (FL-01), `areas` (FL-02), `devices` (FL-03 + `/context` adapter + `metrics` read-only) và `ingestion` (FL-06). Các module còn lại trong danh
sách là ranh giới thiết kế dự kiến, chưa phải tính năng đã triển khai.

- `auth`: tạo Admin bằng CLI riêng, login/session/logout và session/CSRF guard.
- `areas`: CRUD và vòng đời `archive`/`restore` Farm/Zone theo DB (thay thế `HQ_AREA_MANIFEST`).
- `devices`: adapter `/context`, tạo/list/detail/sửa tên, ghi chú Node/Gateway (chọn `farm_id`/`zone_id` từ DB), quản lý Gateway Credential (`/gateways/{id}/credentials*`) và `GET /api/v1/metrics` chỉ đọc. Dùng lại auth guard trong transaction ghi theo thứ tự khóa `Session -> Farm -> Zone -> Gateway -> Node`.
- `ingestion`: `POST /api/v1/ingestion/measurements` dùng `X-API-Key` (adapter auth riêng tái sử dụng `parse_gateway_key_and_digest`, envelope lỗi D04, body cap 64 KiB mặc định), validator Decimal nghiêm ngặt (cấm field lạ), Canonical-v1, per-record transaction theo thứ tự `Farm (id ASC) -> Zone -> Gateway -> Node -> state`, idempotency/conflict, cập nhật `NodeRuntime` và `EvaluationState` (`desired_generation`, `full_replay_required`/`replay_from_t_node`) và trả ACK2 từng record. Không dùng phiên web/CSRF và không chạy worker giám sát.
- Node mới có Gateway NULL vẫn hợp lệ; tạo cùng runtime/state rỗng, không tạo
  config/period/measurement. Không có web API pairing, chuyển/xóa hoặc toggle quyền.

API web dùng prefix `/api/v1`: auth có login/session/logout; devices có `/context`
và GET/POST `/nodes`, `/gateways`, GET/PATCH `/nodes/{id}`, `/gateways/{id}`.
Path ID là khóa BIGINT nội bộ dạng chuỗi; `device_id`/`gateway_id` ở request là
mã nghiệp vụ, phân biệt hoa/thường. Schema/router trong từng module và OpenAPI
runtime là nơi đối chiếu field/HTTP; [README gốc](../../../README.md) dẫn tới test
và các giới hạn kiểm chứng.

Mỗi module dùng `router.py` (giao tiếp), `schemas.py` (input/output), `service.py`
(nghiệp vụ/transaction), `repository.py` (SQL) và `models.py` khi cần.
Route không chứa toàn bộ nghiệp vụ. Repository không tự commit; service điều phối
một transaction chung cho các thay đổi cần nguyên tử. Không dùng session toàn cục
hoặc chia sẻ session với thread. Không gọi endpoint/router của module khác để
thực thi nghiệp vụ nội bộ; tái sử dụng service/helper hoặc guard giao tiếp khi phù hợp.

`core` chứa cấu hình/kết nối/lỗi chung. Chưa có worker: khi hiện thực, một monitoring
thread trong một API process, trạng thái công việc lưu SQL, lifecycle và health phải
được kiểm chứng theo 03.3. Không tạo thread rỗng rồi coi là đã có giám sát.
