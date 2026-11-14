# HQTCSDL — Giám sát nông trại IoT

Nền tảng **FastAPI + SQL Server + React/TypeScript** cho hai Node và một Gateway.
Code cũ đã lưu tại `archive/legacy-before-foundation`; khung này bắt đầu từ
`develop` sau cleanup. Thiết kế nghiệp vụ nằm trên Confluence, code/test nằm ở đây.

| Thư mục | Vai trò |
| --- | --- |
| `backend/app/core` | Cấu hình, DB/session, lỗi và log dùng chung |
| `backend/app/modules` | Module theo 03.3; thêm theo luồng đã review |
| `backend/migrations` | Alembic; hiện chưa có revision/schema nghiệp vụ |
| `backend/tests` | Kiểm tra cơ chế nền tảng; chưa phải kiểm thử SQL thực tế |
| `frontend/src` | React: `app`, `api`, `features` |
| `database` | Quy ước SQL, migration và tài khoản |
| `docs`, `tools` | Hướng dẫn phát triển, kiểm chứng và công cụ tích hợp |

## Bắt đầu

Cần Git, **Python 3.12**, **uv**, **Node.js 24 LTS/npm**. Cài SQL Server native và
Microsoft ODBC Driver 18 khi cần kết nối DB. `pyodbc` không tự cài driver hệ điều hành.

```bash
git clone https://github.com/nhanhehehe/HQTCSDL.git
cd HQTCSDL
git switch develop
cd backend
uv sync --locked
```

Tạo `backend/.env` từ `backend/.env.example` (PowerShell: `Copy-Item .env.example .env`;
Bash: `cp .env.example .env`). Có thể chạy liveness chưa cấu hình SQL; readiness sẽ 503.

Trong thư mục `backend`:

```bash
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Mở terminal khác, từ gốc repo:

```bash
cd frontend
npm ci
npm run dev
```

Mở http://127.0.0.1:5173. Vite proxy `/api` và `/health` sang Backend.

- API docs: http://127.0.0.1:8000/docs
- `/health/live`: tiến trình API phản hồi.
- `/health/ready`: kiểm tra `SELECT 1` trên SQL Server; 503 khi thiếu cấu hình/lỗi.
  Scope hiện tại là `foundation`, `monitoring=not_implemented`; **chưa chứng minh hệ thống
  giám sát sẵn sàng**. Khi có worker phải bổ sung điều kiện sức khỏe worker.

## Kiểm tra trước PR

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run alembic heads
```

Từ `frontend`: `npm run typecheck` và `npm run build`.
`npm ci`/`uv sync --locked` dùng lockfile đã commit; thay dependency cần cập nhật lock
trong cùng PR. CI chạy các kiểm tra này, chưa chạy SQL Server/hardware.

## Bản build phục vụ cùng Backend

Chạy `npm run build` ở `frontend`, rồi chạy Backend như trên. Mở
http://127.0.0.1:8000; FastAPI phục vụ `frontend/dist`. Với máy demo LAN, thay host bằng
`0.0.0.0`, cấu hình firewall và IP theo mục 06. Một API process; không dùng reload
trong demo. Hiện chưa có monitoring worker.

Đọc [hướng dẫn môi trường](docs/development.md), [quy trình nhóm](CONTRIBUTING.md),
[quy ước CSDL](database/README.md) và [bằng chứng kiểm tra](docs/foundation-validation.md).

## Thiết kế áp dụng

- [03.3 — Kiến trúc](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14188568)
- [03.4 — CSDL chung](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14352390)
- [03.5 — Thiết kế luồng](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14319653)
- [03.6 — Nền tảng và quy trình](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14647366)
- [03.1 — Contract Gateway–Backend](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/13729794)

Khung chưa có endpoint nghiệp vụ, ACK2, phiên Admin hoặc schema 19 bảng. Không dùng
trang khởi động hoặc test mock làm bằng chứng đạt AC/NFR. Bước sau là review nền tảng,
kiểm chứng trên máy SQL của nhóm và nhận luồng đầu tiên đủ điều kiện từ 03.5.
