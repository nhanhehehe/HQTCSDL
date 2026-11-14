# Ranh giới module

Theo 03.3: `auth`, `devices`, `ingestion`, `measurements`, `monitoring_config`,
`alerts`, `reports`. Chỉ tạo code module khi nhận task/thiết kế luồng đã review.

Mỗi module dùng `router.py` (giao tiếp), `schemas.py` (input/output), `service.py`
(nghiệp vụ/transaction), `repository.py` (SQL) và `models.py` khi cần.
Route không chứa toàn bộ nghiệp vụ. Repository không tự commit; service điều phối
một transaction chung cho các thay đổi cần nguyên tử. Không dùng session toàn cục
hoặc chia sẻ session với thread. Không import router của module khác để gọi nội bộ.

`core` chứa cấu hình/kết nối/lỗi chung. Chưa có worker: khi hiện thực, một monitoring
thread trong một API process, trạng thái công việc lưu SQL, lifecycle và health phải
được kiểm chứng theo 03.3. Không tạo thread rỗng rồi coi là đã có giám sát.
