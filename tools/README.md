# Công cụ tích hợp — Gateway Simulator (`tools/gateway_simulator`)

Simulator HTTP dòng lệnh mô phỏng hành vi Gateway theo đúng hợp đồng **03.1** (v14) và **FL-06** (v13):
- Hàng đợi bền vững cục bộ (`pending` và `quarantine`) lưu nguyên tử trên đĩa để kiểm chứng phục hồi sau khởi động lại (`F20-AC10/13`).
- Validator toàn bộ `results[]` trước khi giải phóng bất kỳ bản ghi nào (`E06-02`): phát hiện thiếu/trùng `index`, sai kiểu, lệch `device_id`/`boot_id`/`seq`, hoặc mâu thuẫn `status`/`ack`.
- Lập lịch công bằng ưu tiên dữ liệu mới nhưng bảo đảm backlog tiến triển (mặc định `3` lượt mới : `1` lượt backlog, `F20-AC23`).
- Xử lý mất phản hồi (`--simulate-lost-response`), chia nhỏ batch khi `413`, tôn trọng `Retry-After` khi `429`, và không bao giờ in/ghi `X-API-Key` vào log.

## Cách chạy (từ thư mục gốc repo, dùng môi trường Python của `backend/`)

Chỉ trỏ vào Backend đang kết nối **DB test/local riêng** (tuyệt đối không trỏ vào DB đo thật):

```powershell
# 1) Nạp một bản ghi mẫu vào hàng đợi bền vững cục bộ
uv --project backend run python -m tools.gateway_simulator.cli --state .tmp-sim-queue.json enqueue --device-id NODE-SIM-01 --boot-id 1 --seq 1
uv --project backend run python -m tools.gateway_simulator.cli --state .tmp-sim-queue.json enqueue --device-id NODE-SIM-01 --boot-id 1 --seq 2 --time-status UNKNOWN

# 2) Đặt API key vào biến môi trường (không truyền qua tham số dòng lệnh)
$env:HQ_SIM_API_KEY="REPLACE_WITH_ISSUED_GATEWAY_KEY"

# Kịch bản 1: Giả lập MẤT PHẢN HỒI sau khi server commit -> pending vẫn giữ nguyên 2 bản
# 3) Đồng bộ một batch lên Backend local
uv --project backend run python -m tools.gateway_simulator.cli --state .tmp-sim-queue.json sync --base-url http://127.0.0.1:8000 --gateway-id GW-01

# 4) Xem trạng thái hàng đợi
uv --project backend run python -m tools.gateway_simulator.cli --state .tmp-sim-queue.json status

# Kịch bản 2: Gửi lại (idempotent retry) -> Backend trả ALREADY_EXISTS, ack=true -> Simulator giải phóng hàng đợi về pending=0
uv --project backend run python -m tools.gateway_simulator.cli --state .tmp-sim-queue.json sync --base-url http://127.0.0.1:8000 --gateway-id GW-SIM-01
uv --project backend run python -m tools.gateway_simulator.cli --state .tmp-sim-queue.json status

Remove-Item .tmp-sim-queue.json -ErrorAction SilentlyContinue
Remove-Item Env:HQ_SIM_API_KEY -ErrorAction SilentlyContinue
```



## Giới hạn kiểm chứng

Simulator chỉ kiểm chứng hợp đồng HTTP/JSON/ACK2/idempotency ở tầng phần mềm. Kết quả chạy simulator **không thay thế** bằng chứng phần cứng ESP32, LoRa HMAC-SHA256 (`TC-26`), ACK1, Flash/SD thật hoặc thử nghiệm offline 24 giờ (`FL-05` / `F20-AC16`).
