# CSDL và migration

SQL Server/T-SQL là nền tảng chính. Alembic nằm tại `backend/migrations` để dùng cùng
SQLAlchemy metadata và lịch sử revision; SQL đặc thù có thể dùng `op.execute()`.
Không tách thêm runner SQL độc lập hoặc sao chép schema thành nhiều nguồn chuẩn.

Quy trình: thiết kế 03.4/luồng review → đầu mối DB xác nhận → tạo revision có `down_revision`
đúng head → viết migration → kiểm thử SQL Server → PR. Không sửa revision đã áp dụng.
Nếu hai PR tạo hai head phải điều phối phụ thuộc/revision hoặc merge revision có review.
Không tự đánh số lại migration đã triển khai. Seed dữ liệu nền tảng tách fixture/simulator.

Chưa có migration tạo 19 bảng vì đó là thiết kế cần hoàn thiện theo luồng. Hiện Alembic
chỉ là scaffold; chạy heads không có đầu ra là đúng. Chưa có script reset/drop database.
Xem [môi trường](../docs/development.md) trước khi chạy lệnh có ghi dữ liệu.
