# Phân công task cho nhóm 5 người

Đề tài: Nền tảng IoT giám sát nông trại, dùng bo mạch đo nhiệt độ/độ ẩm và gateway mô phỏng UAV.

Nguyên tắc: mỗi thành viên đều có phần code chạy được, testcase riêng, tài liệu kỹ thuật và trách nhiệm tích hợp. Không phân công một người chỉ làm báo cáo.

## 1. Kiến trúc code dùng chung

```text
uav-iot-farm-monitoring/
├── backend/       API, xác thực, nghiệp vụ, cảnh báo
├── frontend/      Web Admin và Operator
├── firmware/      Code ESP32 + cảm biến nhiệt độ/độ ẩm
├── gateway/       Gateway simulator, offline queue, retry, sync
├── simulator/     Mission, waypoint, telemetry và dữ liệu mẫu
├── database/      Schema, migration, seed, backup/restore
├── tests/         Unit, integration, end-to-end, performance
├── docs/          UML, API, giao thức, hướng dẫn
└── evidence/      Log, ảnh, kết quả test
```

Đề xuất stack để giảm xung đột: ESP32/Arduino C++ cho firmware; Python FastAPI hoặc Node.js cho backend; PostgreSQL/MySQL cho database; React hoặc HTML/CSS/JavaScript cho frontend; Python cho gateway simulator và mission simulator. Nhóm có thể thay stack, nhưng phải thống nhất API JSON, Git branch và cách chạy dự án trước khi lập trình.

## 2. Bảng phân công tổng quan

| Thành viên | Mảng chính | Mã yêu cầu chính | Phần code bắt buộc | Đầu ra chính |
|---|---|---|---|---|
| TV1 | Kiến trúc, database và backend nền tảng | F01–F03, một phần F11 | API auth, user, farm, zone, schema, migration, seed | Backend skeleton, database, API contract |
| TV2 | Firmware sensor và gateway simulator | F12, F18–F20 | ESP32 đọc sensor, protocol payload, local queue, retry, ACK, chống trùng | Firmware, gateway, simulator, log offline/sync |
| TV3 | Mission, telemetry và device management | F13–F17 | CRUD UAV/gateway, mission, waypoint, state machine, telemetry simulator | Mission API, simulator, màn hình theo dõi |
| TV4 | Frontend dashboard và biểu đồ | F04–F07, phần F11 | Web Admin/Operator, bản đồ, biểu đồ, filter, gọi API | Giao diện chạy với API thật |
| TV5 | Alert, notification, report và tích hợp kiểm thử | F08–F11, NFR | Rule engine, timeout/pin, web/email, report API/export, test runner | Alert/report code, E2E tests, package triển khai |

## 3. Thành viên 1: Kiến trúc, database và backend nền tảng

### Nhiệm vụ

- Chốt cấu trúc repository, biến môi trường và quy ước API.
- Thiết kế ERD và tạo schema cho users, roles, farms, zones, sensors, measurements, missions, alerts, audit logs.
- Viết migration, seed dữ liệu mẫu và script backup/restore.
- Xây dựng backend skeleton: routing, cấu hình database, error handling, logging và health check.
- Lập trình F01: login/logout và quản lý session/token.
- Lập trình F02: quản lý tài khoản và phân quyền Administrator/Operator.
- Lập trình F03: CRUD farm/zone và kiểm tra phạm vi truy cập.
- Cung cấp API contract để TV2–TV5 tích hợp.

### Code bắt buộc

```text
backend/
├── auth/
├── users/
├── farms/
├── zones/
├── common/
└── main.*
database/
├── migrations/
├── seed.*
└── backup-restore.*
```

### Test và bằng chứng

- Login đúng/sai, logout và phiên hết hiệu lực.
- User không gọi được API admin.
- Không thể đổi `farm_id` để xem dữ liệu farm khác.
- Tạo farm/zone và kiểm tra khóa ngoại.
- Khôi phục database từ backup.

### Phụ thuộc

TV1 phải cung cấp API contract và database seed trước khi các thành viên khác tích hợp. TV1 nhận payload measurement, mission và alert từ các thành viên khác để cập nhật migration nếu cần.

## 4. Thành viên 2: Firmware sensor và gateway simulator

### Nhiệm vụ

- Lập trình ESP32 hoặc bo mạch tương đương để đọc nhiệt độ và độ ẩm từ DHT22/BME280.
- Tạo `sensor_id`, `sequence_no`, timestamp, đơn vị và cờ chất lượng.
- Viết protocol payload giữa sensor và gateway.
- Viết gateway simulator mô phỏng việc phát hiện sensor và thu dữ liệu.
- Lưu dữ liệu cục bộ khi mất Internet.
- Viết hàng đợi `pending/sending/acknowledged/failed`.
- Retry khi mạng khôi phục.
- Gửi batch và xử lý ACK từng bản ghi.
- Chống trùng theo định danh bản đo.
- Ghi log số bản ghi đã thu, chờ gửi, thành công và lỗi.

### Code bắt buộc

```text
firmware/
├── sensor_main.ino
├── sensor_config.h
└── payload.*
gateway/
├── discovery.*
├── collector.*
├── offline_queue.*
├── sync_worker.*
└── gateway_main.*
simulator/
└── sensor-data-generator.*
```

### Test và bằng chứng

- Đọc được nhiệt độ/độ ẩm hợp lệ.
- Payload thiếu trường hoặc sai kiểu bị từ chối.
- Mất mạng vẫn lưu được dữ liệu.
- Reboot gateway không làm mất hàng đợi.
- Mất ACK rồi gửi lại không tạo bản ghi trùng.
- Sensor không đăng ký bị từ chối.
- Sequence gap được phát hiện.

### Phụ thuộc

TV2 dùng endpoint ingest của TV1. TV2 phải thống nhất với TV3 cách gateway gửi mission ID, waypoint và trạng thái collection.

## 5. Thành viên 3: Mission, telemetry và quản lý thiết bị

### Nhiệm vụ

- Lập trình quản lý UAV và gateway.
- Lập trình đăng ký sensor và gán sensor vào zone, phối hợp với API TV1.
- Tạo mission với farm, target sensors và waypoint.
- Xây dựng state machine mission:

```text
draft → ready → in_progress → completed
                         ↘ partially_completed
                         ↘ failed
```

- Ghi collection attempt của từng sensor.
- Viết telemetry simulator cho vị trí, waypoint, pin và thời gian cập nhật.
- Hỗ trợ cập nhật trạng thái/ghi chú thủ công.
- Tổng hợp sensor thu thành công, thất bại và lý do lỗi.

### Code bắt buộc

```text
backend/devices/
backend/missions/
backend/telemetry/
simulator/
├── mission_simulator.*
├── waypoint_runner.*
└── telemetry_generator.*
frontend/operator/
├── MissionCreate.*
├── MissionMonitor.*
└── MissionResult.*
```

### Test và bằng chứng

- Không tạo được mission khi target khác farm.
- Mission có waypoint hợp lệ.
- Mission hoàn thành một phần khi một sensor lỗi.
- Telemetry mất kết nối được hiển thị là dữ liệu cũ.
- Cập nhật trạng thái thủ công có người cập nhật và thời gian.
- Gửi telemetry sai thứ tự không làm lùi trạng thái mới nhất.

### Phụ thuộc

TV3 dùng schema/API nền tảng của TV1 và payload thiết bị của TV2. TV3 cung cấp dữ liệu mission mẫu cho TV4 và TV5.

## 6. Thành viên 4: Frontend dashboard, bản đồ và biểu đồ

### Nhiệm vụ

- Xây dựng layout và routing cho hai nhóm người dùng.
- Lập trình dashboard tổng quan F04.
- Hiển thị farm, zone và sensor trên bản đồ F05.
- Hiển thị nhiệt độ/độ ẩm hiện tại và lịch sử F06.
- So sánh dữ liệu giữa các zone F07.
- Xây dựng giao diện quản lý mission và theo dõi telemetry phối hợp với TV3.
- Hiển thị trạng thái đồng bộ, tuổi dữ liệu, chất lượng dữ liệu và lỗi.
- Dùng API thật, không hard-code kết quả cuối.

### Code bắt buộc

```text
frontend/
├── auth/
├── admin/
│   ├── Dashboard.*
│   ├── FarmMap.*
│   ├── SensorHistory.*
│   └── ZoneCompare.*
├── operator/
├── components/
├── services/api.*
└── styles/
```

### Test và bằng chứng

- Ba trình duyệt Chrome, Edge, Firefox.
- Dashboard không có dữ liệu vẫn hiển thị đúng trạng thái.
- Biểu đồ dùng đúng đơn vị và khoảng thời gian.
- Dữ liệu đến muộn được đặt đúng thời điểm đo.
- Sensor ngoài phạm vi farm không xuất hiện với người dùng trái quyền.
- Sau đồng bộ, dữ liệu xuất hiện trong khoảng thời gian mục tiêu đã đo.

### Phụ thuộc

TV4 chỉ chốt giao diện sau khi TV1 cung cấp API contract. TV4 dùng mission mẫu của TV3, dữ liệu measurement của TV2 và dữ liệu alert của TV5.

## 7. Thành viên 5: Alert, notification, report và tích hợp kiểm thử

### Nhiệm vụ

- Lập trình cấu hình ngưỡng nhiệt độ và độ ẩm.
- Lập trình phát hiện vượt ngưỡng.
- Lập trình timeout khi sensor quá lâu chưa cập nhật.
- Lập trình cảnh báo pin thấp nếu gateway/UAV simulator có dữ liệu pin.
- Xây dựng vòng đời alert: `open → acknowledged → resolved/closed`.
- Gửi notification trong web và email thử nghiệm.
- Retry email lỗi và lưu trạng thái gửi.
- Lập trình báo cáo sensor, device, alert và mission.
- Viết integration test/E2E test cho luồng đầy đủ.
- Đóng gói chạy dự án, kiểm tra cài đặt lại và chuẩn bị evidence.

### Code bắt buộc

```text
backend/alerts/
├── rules.*
├── evaluator.*
├── timeout_worker.*
└── alert_routes.*
backend/notifications/
├── email_sender.*
└── retry_worker.*
backend/reports/
├── sensor_report.*
├── device_report.*
├── alert_report.*
└── mission_report.*
tests/
├── integration/
├── e2e/
└── performance/
scripts/
└── run-demo.*
```

### Test và bằng chứng

- Giá trị đúng bằng, cao hơn và thấp hơn ngưỡng.
- Timeout khi không có bản đo mới.
- Thiếu thông tin pin không bị coi là pin 0.
- Một tình trạng kéo dài không tạo vô hạn cảnh báo.
- Email lỗi được retry.
- Báo cáo khớp dữ liệu database.
- Luồng đầy đủ: sensor → gateway → offline → sync → dashboard → alert → report.

### Phụ thuộc

TV5 tích hợp mã của cả bốn thành viên còn lại. TV5 không tự viết lại chức năng của người khác; chỉ bổ sung adapter, test và xử lý tích hợp khi cần.

## 8. Quy tắc phối hợp để không xung đột code

- Mỗi người làm một branch riêng: `feature/tv1-backend`, `feature/tv2-gateway`, `feature/tv3-mission`, `feature/tv4-frontend`, `feature/tv5-alert-test`.
- Mọi endpoint phải được mô tả trong `docs/api-contract.yaml` hoặc file tương đương trước khi frontend gọi.
- Không tự ý đổi tên field payload sau khi TV2 và TV1 đã tích hợp.
- Mỗi pull request phải có code, cách chạy và testcase liên quan.
- Không commit mật khẩu, API key, email thật hoặc dữ liệu nhạy cảm.
- Dùng một bộ seed chung để mọi người kiểm thử cùng dữ liệu.
- Mỗi thành viên review ít nhất một phần code của thành viên khác.
- Merge theo thứ tự: database/backend nền tảng → firmware/gateway → mission → frontend → alert/test/package.

## 9. Lịch làm việc đề xuất

| Giai đoạn | Việc chính | Người chịu trách nhiệm |
|---|---|---|
| Ngày 1–2 | Chốt stack, payload, ERD, API và cấu trúc repo | Cả nhóm, TV1 điều phối |
| Ngày 3–7 | Backend auth/farm/zone; firmware đọc sensor; skeleton frontend; mission model; alert model | TV1–TV5 làm song song |
| Ngày 8–12 | Gateway offline/sync; mission simulator; dashboard; rule engine; database integration | TV2–TV5 tích hợp với TV1 |
| Ngày 13–16 | Luồng đầy đủ, biểu đồ, map, notification, report | Cả nhóm |
| Ngày 17–19 | Test mất mạng, mất ACK, dữ liệu sai, phân quyền, trình duyệt | TV5 điều phối, mọi người sửa phần mình |
| Ngày 20 | Đóng gói, chạy lại từ đầu, chụp bằng chứng, hoàn thiện tài liệu và demo | Cả nhóm |

## 10. Tiêu chí hoàn thành của từng người

Một thành viên chỉ được coi là hoàn thành khi có đủ:

1. Code chạy được trong repository chung.
2. API hoặc module có hướng dẫn sử dụng.
3. Testcase và kết quả test.
4. Dữ liệu/log/bằng chứng có thể đối chiếu.
5. Tài liệu ngắn mô tả thiết kế, giới hạn và cách tích hợp.

## 11. Luồng tích hợp cuối cùng

```text
TV1 tạo database và API nền tảng
→ TV2 gửi dữ liệu nhiệt độ/độ ẩm qua gateway
→ TV3 gắn dữ liệu vào mission và waypoint
→ TV1 lưu measurement và mission result
→ TV4 hiển thị dashboard, bản đồ và biểu đồ
→ TV5 đánh giá ngưỡng, tạo alert, gửi email và xuất report
→ Cả nhóm chạy E2E test và hoàn thiện demo
```

Tên thành viên, MSSV và thời lượng thực tế chưa được đề bài cung cấp; nhóm cần thay `TV1`–`TV5` bằng thông tin thật trước khi nộp.
