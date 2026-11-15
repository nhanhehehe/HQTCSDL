# Cơ chế Backend dùng chung — G0-T04

Nguồn: [03.5.1, mục 2/8, Confluence v13](https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14680313).
Đây là code nền cho API web; chưa có auth, hồ sơ, ingestion hoặc schema nghiệp vụ.

## DTO và codec

Schema web kế thừa `WebModel`: strict JSON types, cấm trường lạ. `bool` không thay
`int`; ID không tự ép JSON number sang string. Dùng `model_fields_set` hoặc
`model_dump(exclude_unset=True)` cho PATCH để phân biệt thiếu trường và null.
Module phải tự quy định field nào nhận null/immutable và nghiệp vụ no-op.

```python
from app.core.contracts import InternalId, RowVersion, WebModel

class ProfilePatch(WebModel):
    expected_row_version: RowVersion
    notes: str | None = None

# Bỏ notes: giữ nguyên. Gửi notes=null: xóa ghi chú.
# expected_row_version stale phải kiểm trước no-op ở service/repository.
```

Các kiểu trong `app/core/contracts.py`:

| Kiểu/hàm | Quy cách |
| --- | --- |
| `InternalId` | Decimal string chuẩn, 1..2^63−1; không nhận int/bool/leading zero |
| `BigIntText` | Như trên nhưng nhận 0, dùng cho counter/boot/epoch web có miền phù hợp |
| `web_integer` | Chuyển int SQL không âm thành chuỗi; không nhận float/bool |
| `DecimalText` | Nhận Python Decimal hoặc chuỗi decimal hữu hạn; JSON output luôn string; không qua float, không làm tròn |
| `RowVersion` | Bytes nội bộ đúng 8 byte; JSON input/output base64 chuẩn có padding, cấm noncanonical pad bits |
| `RangeTime` | RFC3339 có timezone → UTC, giữ microsecond cho phép tính biên range |
| `BackendTime` | Datetime có timezone → JSON UTC với Z và millisecond; Backend timestamps dùng clock bên dưới |
| `PageSize` | JSON int strict 1..200 |
| `page_size_query` | Parse query text có chủ đích, default 50; module chuyển ValueError thành WebError 400 |

Không dùng `BigIntText` để bỏ qua miền nhỏ hơn của từng field; raw epoch có max
253402300799 theo contract. `DecimalText` không tự kiểm scale/miền của metric.
Nó là kiểu response/decimal-text, không thay validator numeric wire của ESP.
Monitoring lower/upper đang có câu number ở mục 2 và string ở ví dụ mục 10 của
nguồn: chưa tự chọn DTO request nghiệp vụ đó trong T04, phải đối chiếu FL-08 khi làm.

## Clock

`utc_now()` trả aware UTC và cắt microsecond xuống millisecond, phù hợp DATETIME2(3).
Service nhận clock có thể thay bằng fixture khi test. Không lấy thời gian local naive.
`sql_utc()` chỉ dùng tại biên đọc cột SQL DATETIME2 được quy ước lưu UTC để gắn UTC
cho datetime naive; không dùng nó để chấp nhận timestamp request thiếu timezone.

## Lỗi và cache

API web `/api/v1` (trừ `/api/v1/ingestion` và các đường con) có lỗi JSON:

```json
{"error":{"code":"VERSION_CONFLICT","message":"Đọc lại dữ liệu","retryable":false},"request_id":"<backend-generated>"}
```

Service ném `WebError(status, code, message, details=...)` bằng thông tin công khai.
Không truyền exception SQL, credential hoặc request body vào message/details.
Request validation trả 400 INPUT_INVALID, không echo body/validation input. SQLAlchemy
error chưa được xử lý riêng tại web trả 503 STORAGE_UNAVAILABLE; service vẫn phải
phân loại UQ/version/no-op theo nghiệp vụ, không dùng 503 thay mọi xung đột.
Lỗi không dự kiến trả 500 INTERNAL_ERROR. 503 có retryable=true; các lỗi còn lại false.
Không tự retry mutation vì mất response là unknown outcome.

Backend sinh request ID mới, gắn vào header X-Request-ID và error body. API response
được đặt Cache-Control: no-store, gồm success/errors. Logout 204 phải không body.
Health giữ DTO foundation hiện có; lỗi ngoài web giữ problem+json của scaffold.

**Ingestion:** không kế thừa WebModel/codecs web hoặc dùng WebError làm ACK2.
T04 không đăng ký endpoint ingestion thật và không áp handler validation 400 web
vào ingestion. T10 phải parse/validate raw envelope/items và trả contract 03.1,
không để FastAPI tự trả 422 cho payload ingestion. Test hiện tại chỉ chứng minh
response riêng của route ingestion không bị bọc thêm error/request_id web;
chưa chứng minh validator/ACK2 đã hiện thực. Header X-Request-ID được phép có,
không thêm request_id bắt buộc vào wire body.

## Transaction

Giữ `Database.sessions` của scaffold: service sở hữu transaction, repository không
commit các phần con. Route nhận DTO/kết quả rồi serialize sau khi service đã commit.

```python
with database.sessions.begin() as session:
    # Repositories nhận cùng session; lỗi thoát context thì rollback.
    # Kiểm version/guard, ghi dữ liệu và state trong transaction của service.
    ...
# Chỉ trả thành công khi commit đã hoàn tất.
```

Mỗi request/thread mở session riêng. Không dùng session toàn cục, không tự retry
commit khi chưa biết kết quả. SNAPSHOT phải chọn trước câu SQL đầu tiên của
transaction trên connection dành riêng; không đặt SET sau autobegin hoặc đổi
isolation giữa transaction. Kiểm chứng qua Python/SQL Server thật thực hiện ở
M1/luồng tương ứng; test contract T04 không tuyên bố khóa/rollback nghiệp vụ đạt.

## Kiểm chứng

Từ backend/: `uv sync --locked`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run pytest`, `uv run alembic heads`.
Test routes minh họa chỉ tạo trong fixture test, không đưa endpoint demo vào API thật.
Không thay dependency lock hoặc schema database trong T04.
