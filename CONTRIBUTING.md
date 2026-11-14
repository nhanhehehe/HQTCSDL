# Cách làm việc chung

## Nhánh và task

- `main`: bản ổn định; nhận PR `develop → main`.
- `develop`: tích hợp; nhận PR từ nhánh task.
- Mỗi task có issue ghi phạm vi, thiết kế/phiên bản, AC/NFR, phụ thuộc và tiêu chí đạt.
- Nhánh: `<type>/<issue>-<mo-ta-khong-dau>`; type: feat, fix, refactor, test, docs, chore.

Ví dụ (thay số issue bằng task thật):

```bash
git fetch origin
git switch -c feat/123-ingestion-validation origin/develop
```

Trước đó working tree phải sạch. Nhánh có sẵn thì switch, không reset/tạo đè.
Đồng bộ trong lúc làm bằng `git fetch origin` rồi `git merge origin/develop`.
Giải quyết conflict, đọc diff và chạy lại kiểm tra phần bị ảnh hưởng.

Commit: `<type>(<scope>): <description>`. Stage đúng file, không commit `.env`, token,
DB/backup hoặc dữ liệu đo thật. Push nhánh task và mở PR **vào develop**.

## Review và merge

PR dùng mẫu, liên kết issue/thiết kế và ghi lệnh/kết quả test cùng phần chưa chạy.
Ít nhất một approval hợp lệ của người khác, giải quyết conversation, CI đạt, review lại
khi commit mới làm thay đổi nội dung. Nhân điều phối review API/SQL/cơ chế chung;
GitHub không cho tác giả tự approve PR. Muốn bắt buộc một người cụ thể phải cấu hình
rules/CODEOWNERS phù hợp và bố trí tác giả/reviewer khác nhau.

- Task → develop: ưu tiên squash merge.
- Develop → main: **Create a merge commit**, không squash/rebase.
- Nếu main thay đổi riêng: PR main → develop, dùng merge commit.
- Không direct push/force-push/xóa main hoặc develop trong công việc hằng ngày.

CI kiểm tra code/hướng nhánh không thay thế quyền repository hoặc branch rules.
Tên check: `branch-policy`, `backend`, `frontend`. Chỉ đặt required check sau khi check
thật sự xuất hiện và chạy đạt trên PR. Không tự thay đổi rules trong PR source.

## Ranh giới code

Backend theo `backend/app/modules/README.md`. FE theo chức năng; dùng chung API client,
không chứa credential SQL/key Gateway. API/DTO nghiệp vụ lấy từ thiết kế đã review.
Service sở hữu transaction; migration schema dùng chung phải có đầu mối DB review.
Không dùng SQLite để tuyên bố SQL Server/khóa/đồng thời đã đạt.

Khi thiết kế thay đổi, cập nhật nguồn Confluence và nêu task/test bị ảnh hưởng.
Không ghi test đạt khi chỉ mới viết tình huống kỳ vọng hoặc chưa có môi trường chạy.
