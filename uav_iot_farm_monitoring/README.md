# Nền tảng IoT giám sát nông trại

Phiên bản hiện thực Phase 4-6: ESP32 gateway, FastAPI + SQL Server và giao diện HTML/CSS/JavaScript.

## Kiến trúc

```text
ESP32 node -> ESP32 gateway -> FastAPI -> SQL Server -> Web dashboard
```

Gateway chạy ở chế độ AP+STA: node gửi JSON đến gateway; gateway xác thực, lưu hàng đợi LittleFS và đồng bộ batch đến FastAPI khi backend truy cập được.

## Chạy backend và frontend trên localhost

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m app.seed
uvicorn app.main:app --reload
```

Mở `http://127.0.0.1:8000`. FastAPI phục vụ luôn HTML/CSS/JavaScript nên không cần XAMPP hay HTTPS khi chạy local. Tài khoản mẫu: `admin / Admin@123` và `operator / Operator@123`.

## SQL Server

Mặc định `.env.example` dùng SQL Server local với Windows Authentication và `TrustServerCertificate=yes`. Có thể chạy `database/001_schema.sql` trong SSMS trước, hoặc chạy `python -m app.seed` để SQLAlchemy tạo các bảng và dữ liệu mẫu.

## Gateway ESP32

Sao chép `gateway/include/config.example.h` thành `gateway/include/config.h`, điền Wi-Fi/backend/key, rồi mở thư mục `gateway` bằng PlatformIO và nạp firmware. Gateway nhận:

```text
POST /node/discover
POST /node/measurements
```

Node gửi header `X-Node-Key` và JSON chứa `sensor_id`, `sequence_no`, năm thông số môi trường và `measured_at`.

## Kiểm thử

```powershell
cd backend
pytest -q
```
