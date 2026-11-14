# Quy trình làm việc HQTCSDL

## 1. Nhánh và hướng PR

- `main`: phiên bản ổn định; giữ làm default branch. Không push trực tiếp, force-push hoặc xóa nhánh.
- `develop`: tích hợp hằng ngày; không push trực tiếp, force-push hoặc xóa nhánh.
- Nhánh task tạo từ `origin/develop`; PR nhánh task → `develop`.
- Sau tích hợp và kiểm thử, PR `develop` → `main`. Không đưa nhánh tính năng thẳng vào main.
- Nếu main có thay đổi cần đồng bộ, mở PR `main` → `develop`; không reset hoặc force-push develop.

## 2. Tên nhánh và commit

Tên nhánh: `<type>/<issue-number>-<short-description>`. Type: `feat|fix|refactor|test|docs|chore`; chữ thường, không dấu, mô tả bằng dấu gạch ngang. Ví dụ giả định: `feat/123-ingestion-validation`.

Ngoại lệ thiết lập ban đầu: `chore/bootstrap-repository-workflow`; không dùng ngoại lệ này cho tính năng thông thường. Không đổi tên hoặc xóa nhánh cũ của người khác khi chưa phối hợp.

Commit: `<type>(<scope>): <description>`, ví dụ `fix(ingestion): preserve duplicate acknowledgements`. Một commit tập trung một thay đổi liên quan; không gom việc không liên quan hoặc ghi “done/update” thiếu ngữ cảnh.

## 3. Bắt đầu và cập nhật công việc

1. Nhận issue/task, đọc đúng phiên bản thiết kế và AC; xác định input/output, phụ thuộc và tiêu chí nghiệm thu. Chưa có thiết kế đủ rõ thì dùng draft PR, không tự chốt nghiệp vụ.
2. Đảm bảo working tree sạch (commit hoặc stash phần đang làm), fetch rồi tạo nhánh từ develop:

```bash
git fetch origin
git switch -c feat/123-ingestion-validation origin/develop
```

Tên trên chỉ là ví dụ; thay bằng issue thật. Nếu nhánh đã có thì switch nhánh đó, không tạo lại hoặc reset.

3. Code trong phạm vi task, chạy kiểm tra liên quan. Khi cần cập nhật develop vào nhánh task:

```bash
git fetch origin
git merge origin/develop
```

Giải conflict, đọc diff và chạy lại kiểm tra bị ảnh hưởng. Không force-push nhánh chung.

4. Đọc `git diff`, stage đúng file, commit và push nhánh task; mở PR với base `develop`. Không stage credential/config riêng, dump hoặc dữ liệu đo thật.

## 4. PR và review

Dùng PR template. Ghi task, nguồn/phiên bản thiết kế, AC, hành vi thay đổi và bằng chứng kiểm tra. “Chưa chạy” khác “đạt”; mock/SQLite khác SQL Server thật. Không công bố đã đạt AC chỉ vì CI routing xanh.

Mỗi PR cần ít nhất một approval của người khác và xử lý các conversation chặn merge. Có commit làm thay đổi nội dung sau approval thì cần review lại. Các điều kiện này phải được bật bằng branch rules; tài liệu không tự cưỡng chế chúng.

Thay API, transaction, migration hoặc cơ chế chung cần người phụ trách phần dùng chung review. Không tự thêm reviewer/assignee khi nhóm chưa phân công.

## 5. Merge và phát hành

- Task → develop: ưu tiên squash merge, đặt squash commit rõ nghĩa.
- Develop → main: dùng **Create a merge commit** để giữ quan hệ lịch sử hai nhánh lâu dài; không squash hoặc rebase lần promote này.
- Main → develop khi cần đồng bộ: dùng merge commit.
- Người merge kiểm tra đúng head/base và commit đã test; chưa đủ test thì giữ draft/chờ review.
- Chỉ xóa nhánh task sau merge khi không còn PR/phụ thuộc. Không xóa main/develop hoặc nhánh cũ chỉ vì đã thiết lập workflow mới.

## 6. API, CSDL, môi trường và test

- Contract Gateway–Backend: 03.1; thiết kế luồng: 03.5. Ghi thay đổi tại nguồn có thẩm quyền trước khi các bên dùng contract mới.
- Không coi schema cũ là migration mới đã duyệt. Migration được đặt thứ tự và review chung; không sửa file đã được áp dụng để viết lại lịch sử.
- Dev/test/demo có cấu hình và database riêng; config mẫu không có bí mật thật. Script xóa/reset dữ liệu chỉ dùng DB test được xác định rõ.
- Người triển khai ghi lệnh, môi trường, kết quả và giới hạn test trong PR. Kiểm thử SQL Server/đồng thời/restart/hardware theo luồng, không thay bằng một health check.
- Không đổi layout hoặc thư viện hàng loạt trong PR quy trình này. Công cụ/lockfile, khung Backend/FE và migration runner sẽ được chốt khi dựng nền tảng sau review source hiện có.

## 7. Thiết lập bảo vệ cần bật

Main và develop: require PR, ít nhất một approval, dismiss stale approvals, resolve conversations, chặn force-push và xóa; không bypass cho công việc hằng ngày. Sau khi workflow đã chạy thành công trên repo, đặt `branch-policy` làm required check và yêu cầu cập nhật với base khi phù hợp.

CI hiện chỉ kiểm tra hướng PR/tên nhánh. Nhánh release vào main phải từ develop cùng repo. Main chỉ ổn định cho bản phát hành mới sau review và kiểm thử; code đã có trước khi bật rules không tự được nghiệm thu.
