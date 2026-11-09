# Workflow đề tài: Nền tảng IoT có UAV hỗ trợ giám sát và thu thập dữ liệu cảm biến nông trại

Ngày soạn: 12/10/2026.

Nguồn yêu cầu: `C:/Users/User/OneDrive - ut.edu.vn/đề bài nhóm.md`.

Mẫu bố cục tham khảo: `C:/Users/User/OneDrive - ut.edu.vn/Documents/ChatGPT/An Ninh Mạng/de-tai-2-x800-web-security-mapping/docs/01-workflow.md`.

Trạng thái: phân tích và kế hoạch thực hiện; chưa triển khai hoặc kiểm thử hệ thống. Các mô hình dữ liệu, trạng thái, hợp đồng giao tiếp và lịch sprint bên dưới là đề xuất thiết kế, không phải nội dung đã được đề bài ấn định.

## 1. Mục tiêu và phân tích bài toán

Xây dựng hệ thống thu thập dữ liệu cảm biến phân tán tại nông trại bằng gateway gắn trên UAV, lưu được khi mất Internet, đồng bộ về máy chủ và cung cấp giao diện quản lý, giám sát, cảnh báo, báo cáo.

Ba vấn đề cần giải quyết:

1. Sensor nằm ở nhiều khu vực; việc thu thập thủ công tốn thời gian.
2. Kết nối Internet không ổn định; dữ liệu phải được giữ tại sensor/gateway và truyền lại khi có thể.
3. Người quản lý cần biết tình hình môi trường, thiết bị và kết quả từng nhiệm vụ thu thập.

Chuỗi nghiệp vụ chính:

```text
Sensor đo và lưu dữ liệu
-> Operator lập nhiệm vụ thu thập
-> UAV mang gateway đến gần sensor
-> Gateway phát hiện, xác thực và thu thập
-> Gateway lưu bền vững tại chỗ
-> Có mạng: đồng bộ về backend
-> Backend kiểm tra, chống trùng và lưu dữ liệu
-> Dashboard/bản đồ/biểu đồ cập nhật
-> Đánh giá ngưỡng, tạo cảnh báo và thông báo
-> Admin xử lý cảnh báo, xem báo cáo
```

Phạm vi bắt buộc theo đề:

- Backend APIs và web phục vụ Farm Administrator, UAV and Device Operator.
- Quản lý farm, zone, sensor node, UAV, mobile gateway và mission.
- Prototype sensor node và mobile gateway gắn trên UAV.
- Thu thập, kiểm tra, lưu offline, tự đồng bộ và chống trùng.
- Bản đồ, dữ liệu hiện tại/lịch sử, so sánh giữa các zone.
- Cảnh báo theo ngưỡng, quá hạn dữ liệu, pin yếu; thông báo web/email.
- Báo cáo sensor, device, alert và mission.
- Mã nguồn, gói triển khai, tài liệu, kiểm thử và minh chứng.

Ngoài phạm vi ban đầu theo đề: AI nâng cao, phân tích ảnh cây trồng và điều khiển bay UAV trực tiếp từ nền tảng.

Điểm cần hiểu đúng:

- UAV đóng vai trò mang gateway di động; không mặc định phải xây autopilot.
- Theo dõi UAV phụ thuộc telemetry thực sự nhận được. Mất telemetry phải hiển thị dữ liệu cũ/mất kết nối, không giả lập vị trí rồi trình bày là thời gian thực.
- “Dữ liệu hiện tại” là bản đo hợp lệ mới nhất đã biết; cần hiển thị tuổi dữ liệu vì sensor có thể chưa được UAV ghé thu thập.
- Mốc khoảng 10 giây tính từ dữ liệu vừa được đồng bộ về hệ thống đến khi xuất hiện trên dashboard, không phải từ thời điểm sensor đo trong vùng mất mạng.

## 2. Thiết kế mô hình triển khai và môi trường lab

```text
Sensor nodes tại các zone
  | Đo + lưu tạm + trả dữ liệu khi gateway yêu cầu
  | Kênh liên lạc cục bộ, có xác thực thiết bị
  v
Mobile gateway gắn trên UAV
  | Collection engine + local storage + hàng đợi đồng bộ
  | Internet có thể gián đoạn
  v
Backend APIs
  | Xác thực, phân quyền, ingest, quản lý mission, truy vấn
  +--> Database trung tâm
  +--> Tác vụ nền: timeout, cảnh báo, retry thông báo
  +--> Dịch vụ email
  v
Web cho Admin và Operator
     Bản đồ, dashboard, biểu đồ, mission, cảnh báo, báo cáo

Nguồn telemetry UAV -> Bộ tiếp nhận telemetry -> Backend -> Web
Operator -> Cập nhật trạng thái/ghi chú thủ công -> Backend
```

Đề xuất phát triển qua hai môi trường:

| Môi trường | Thành phần | Mục đích và giới hạn |
|---|---|---|
| Lab phần mềm | Web, backend, database, sensor simulator, gateway chạy trên máy thử | Làm luồng dữ liệu và tái hiện lỗi có kiểm soát; chưa chứng minh thu thập vô tuyến hoặc tích hợp UAV thật |
| Prototype tích hợp | Sensor node thật, gateway thật, UAV mang gateway và nguồn telemetry nếu có | Đo khả năng thu thập, lưu offline và đồng bộ thực tế; đáp ứng đầu ra phần cứng của đề |

Simulator là công cụ phát triển và kiểm thử. Việc thay toàn bộ prototype bằng mô phỏng chỉ được coi là đủ đầu ra khi giảng viên chấp nhận thay đổi phạm vi.

Các lựa chọn cần chốt ở giai đoạn đầu: phần cứng sẵn có, giao thức sensor–gateway, nguồn telemetry, hệ quản trị CSDL, nền tảng backend/frontend, môi trường triển khai. Chưa có thông tin ngân sách và thiết bị nên workflow không ấn định hãng hoặc phiên bản phần mềm.

Các nhóm công cụ cần có:

| Nhóm công cụ | Dùng để làm gì |
|---|---|
| IDE, Git và bảng công việc | Lập trình, quản lý phiên bản, theo dõi sprint |
| Công cụ UML và ERD | Vẽ mô hình hệ thống, thiết kế dữ liệu |
| API client và bộ chạy kiểm thử | Kiểm API, phân quyền, dữ liệu sai, gửi lại batch |
| Công cụ quản trị CSDL | Kiểm bản ghi, khóa duy nhất, truy vấn và backup/restore |
| Công cụ firmware và log thiết bị | Nạp sensor, xem log gateway, đối chiếu dữ liệu thu thập |
| Browser DevTools và công cụ đo tải | Kiểm giao diện, thời gian đáp ứng và cập nhật dữ liệu |
| Hộp thư thử nghiệm, bộ thu log/metrics | Kiểm thông báo và lưu minh chứng hoạt động |

## 3. Tổ chức thư mục và đầu ra của từng phần

Cấu trúc dự kiến khi triển khai:

```text
uav-iot-farm-monitoring/
├── backend/                 API, xử lý dữ liệu và tác vụ nền
├── frontend/                Web Admin và Operator
├── gateway/                 Thu thập, local storage, sync worker
├── firmware/                Chương trình sensor node
├── simulator/               Dữ liệu cảm biến và telemetry giả lập
├── database/                ERD, migrations, seed, backup/restore scripts
├── config/                  Cấu hình mẫu, không chứa bí mật thật
├── tests/                   Unit, integration, end-to-end, performance
├── scripts/                 Khởi chạy lab, tạo dữ liệu và demo
├── docs/                    Yêu cầu, UML, API, giao thức, hướng dẫn
├── evidence/                Ảnh, log, kết quả đo và test results
└── reports/                 Báo cáo cuối và slide thuyết trình
```

Mỗi phần hoàn thành cần có: yêu cầu liên quan → thiết kế/cơ chế → mã nguồn/cấu hình → testcase → kết quả thực tế → bằng chứng → giới hạn.

## 4. Phân tích tác nhân, use case và yêu cầu

| Tác nhân | Trách nhiệm | Mã yêu cầu |
|---|---|---|
| Farm Administrator | Tài khoản, farm/zone, dashboard, dữ liệu, cảnh báo, cấu hình và báo cáo | F01–F11 |
| UAV and Device Operator | Sensor, UAV/gateway, lập và theo dõi mission, ghi chú, xem kết quả | F12–F17 |
| Sensor Collection Engine | Phát hiện/xác thực sensor, thu thập/kiểm tra, offline/sync | F18–F20 |

Sensor Collection Engine là tác nhân hệ thống, không phải người dùng cần một màn hình đăng nhập. Operator vẫn cần xác thực để thao tác; đây là yêu cầu thiết kế suy ra từ kiểm soát truy cập, dù F01 được viết dưới vai trò Admin.

Cách thực hiện:

1. Tách F01–F20 thành backlog; giữ nguyên mã để truy vết.
2. Viết use case: tác nhân, tiền điều kiện, đầu vào, luồng chính, luồng lỗi, hậu điều kiện.
3. Lập ma trận quyền theo chức năng và farm được phân công. Quyền giữa hai vai trò cần thống nhất; không tự mặc định Operator được sửa ngưỡng hoặc quản lý tài khoản.
4. Gắn acceptance criteria và mã testcase vào từng yêu cầu.
5. Làm rõ các yêu cầu chưa định lượng: tải người dùng, số sensor, tần suất đo, thời gian offline, dung lượng lưu, lịch backup và cửa sổ đo uptime.

Đầu ra: đặc tả yêu cầu, use case, ma trận quyền, backlog và ma trận truy vết.

## 5. Thiết kế UML, dữ liệu và hợp đồng giao tiếp

Các mô hình UML 2.0 đề yêu cầu:

| Mô hình | Nội dung cần thể hiện |
|---|---|
| Use Case Diagram | Ba tác nhân và các nhóm chức năng F01–F20; nêu rõ biên hệ thống |
| Class Diagram | Farm, Zone, SensorNode, SensorChannel, Measurement, Gateway, UAV, Mission, Alert và quan hệ |
| Sequence Diagram | Login; lập mission; xác thực/thu thập; offline/sync/retry; phát sinh và xử lý cảnh báo |
| Activity Diagram | Luồng nhiệm vụ, thu thập thất bại, đồng bộ từng phần, xử lý cảnh báo |
| Component Diagram | Web, API, collection engine, sync worker, alert worker, database, email, telemetry adapter |
| Deployment Diagram | Browser, máy chủ, sensor, gateway trên UAV và các đường kết nối |

ERD và tài liệu API bổ sung cho UML; ERD không thay thế Class Diagram.

Nhóm bảng đề xuất:

| Nhóm | Bảng/thực thể chính | Vai trò |
|---|---|---|
| Truy cập | users, roles, user_roles, farm_memberships | Xác thực, phân quyền và phạm vi farm |
| Không gian | farms, zones | Farm, vùng và vị trí/ranh giới |
| Thiết bị | sensor_nodes, sensor_types, sensor_channels, uavs, gateways | Quản lý thiết bị và nhiều đại lượng trên một node |
| Thiết bị theo thời gian | device_assignments, device_status_events | Lịch sử gán zone/gateway, trạng thái, pin và last_seen |
| Mission | missions, mission_targets, waypoints, collection_attempts, mission_events | Kế hoạch, kết quả từng node/điểm và ghi chú |
| Telemetry | telemetry_records | Vị trí UAV, pin, waypoint, thời điểm nhận |
| Dữ liệu | measurements, ingestion_batches, ingestion_errors | Bản đo, truy vết batch, lỗi đầu vào |
| Cảnh báo | alert_rules, alerts, alert_actions | Ngưỡng, vòng đời và xử lý cảnh báo |
| Thông báo | notifications, notification_deliveries | Thông báo web/email và lịch sử retry |
| Truy vết | audit_logs | Ai đã thay đổi cấu hình, mission hoặc dữ liệu quản trị |

Gateway cần kho cục bộ riêng cho bản ghi đã thu, hàng đợi đồng bộ, số lần retry, lỗi gần nhất và trạng thái xác nhận.

Quy tắc dữ liệu cần thiết kế ngay:

- Tách `measured_at` (sensor đo), `collected_at` (gateway nhận), `received_at` (server nhận); lưu thêm vị trí thu thập và nguồn vị trí. Không thay thời điểm đo bằng thời điểm đồng bộ.
- Mỗi bản đo có định danh ổn định từ nguồn, chẳng hạn `(node_id, boot_id, sequence_no, channel_id)`. Khóa không được đổi sau mỗi lần gửi lại; cần quy tắc chống tái sử dụng ID sau reboot.
- Lưu đơn vị, loại đại lượng, cờ chất lượng và thời gian chuẩn hóa; đánh dấu đồng hồ sensor không đáng tin thay vì lặng lẽ sửa lịch sử.
- Lịch sử phải giữ đúng zone tại thời điểm đo/thu theo quy tắc đã thống nhất; chuyển node sang zone mới không được làm thay đổi báo cáo cũ.
- Mission target và waypoint là hai khái niệm khác nhau: waypoint là điểm trên tuyến, target là sensor cần thu thập.
- Bảo vệ quan hệ cùng farm, thứ tự waypoint và khóa ngoại. Ưu tiên ngừng sử dụng thiết bị thay vì xóa lịch sử liên quan.

Hợp đồng giao tiếp phải quy định: version payload, ID thiết bị, xác thực, ID bản ghi, timestamp, đơn vị, giới hạn batch, mã lỗi và ACK từng bản ghi.

Đầu ra: bộ UML, ERD, từ điển dữ liệu, API contract, giao thức sensor–gateway và quy tắc đồng bộ.

## 6. Xây dựng nền tảng tài khoản và quản lý farm/zone

Yêu cầu: F01, F02, F03.

Cơ chế:

- Đăng nhập/đăng xuất, lưu mật khẩu đã băm, quản lý phiên.
- Admin tạo/cập nhật/khóa tài khoản và phân quyền.
- Quản lý farm/zone, vị trí và phạm vi truy cập.
- Kiểm quyền ở backend, ghi log thay đổi quan trọng.

Kiểm thử:

- Login đúng/sai; sau logout phiên cũ không truy cập được API bảo vệ.
- Operator gọi API quản lý tài khoản trái quyền phải bị từ chối.
- Người dùng đổi ID farm trong request không xem/sửa được farm ngoài quyền.
- Không xóa farm/zone gây mất lịch sử hoặc tạo dữ liệu mồ côi.

Đầu ra: nền tảng đăng nhập, quản trị tài khoản, farm/zone và dữ liệu mẫu cho các phần sau.

## 7. Quản lý sensor node, UAV và gateway

Yêu cầu: F12, F13; cung cấp dữ liệu cho F04, F05, F09.

Cơ chế:

- Đăng ký node, cấu hình kênh đo và đơn vị, gán vào zone.
- Quản lý UAV, gateway, quan hệ gắn gateway trên UAV và lịch sử thay đổi.
- Cấp định danh/thông tin xác thực riêng cho thiết bị; có khả năng thu hồi.
- Lưu tọa độ, tình trạng, pin và lần liên lạc gần nhất nếu thiết bị cung cấp.
- Phân biệt “đang hoạt động”, “lâu chưa thấy”, “ngừng sử dụng” theo quy tắc đã thống nhất.

Kiểm thử: trùng mã thiết bị; gán sai farm/zone; thiết bị bị thu hồi; chuyển zone; thay gateway; thiếu thông tin pin hoặc vị trí.

Đầu ra: danh mục thiết bị dùng được cho mission và danh sách thiết bị được phép thu thập.

## 8. Lập nhiệm vụ, theo dõi và tổng hợp kết quả UAV

Yêu cầu: F14–F17.

Luồng thực hiện:

```text
Chọn farm
-> Chọn UAV/gateway
-> Chọn sensor targets
-> Tạo tuyến và waypoint có thứ tự
-> Kiểm tra tính hợp lệ
-> Lưu kế hoạch nhiệm vụ
-> Thực hiện bằng hệ thống vận hành UAV bên ngoài
-> Nhận telemetry hoặc cập nhật thủ công
-> Tổng hợp điểm thu thành công/thất bại
```

Trạng thái đề xuất: `draft -> ready -> in_progress -> completed / partially_completed / failed`; cho phép `cancelled` ở các bước phù hợp và lưu lý do. Cần quy định quyền chuyển trạng thái và tiêu chí kết thúc trước khi lập trình.

Mỗi collection attempt ghi node, mission, gateway, thời gian, vị trí, số bản ghi và lỗi. Tách kết quả thu thập với trạng thái đồng bộ: gateway có thể thu thành công nhưng dữ liệu vẫn đang chờ Internet.

Theo dõi: vị trí, pin, waypoint, tiến độ, tuổi telemetry và nguồn cập nhật. Cập nhật thủ công phải có người thực hiện, thời gian và ghi chú; dữ liệu telemetry đến muộn không tự ghi đè trạng thái cuối đã xác nhận.

Kiểm thử: mission không có target; target khác farm; mất telemetry; cập nhật tay; thu thất bại một phần; thu lại một target; hủy nhiệm vụ; telemetry đến sai thứ tự.

Đầu ra: màn hình kế hoạch, màn hình theo dõi và kết quả nhiệm vụ có lý do lỗi từng điểm. Không có chức năng điều khiển bay trực tiếp trong phạm vi này.

## 9. Thu thập và xác thực dữ liệu cảm biến

Yêu cầu: F18, F19.

```text
Gateway tiếp cận vùng sensor
-> Phát hiện node
-> Kiểm tra đã đăng ký và còn hiệu lực
-> Xác thực bằng thông tin bí mật/chứng thực đã cấp
-> Yêu cầu bản ghi chưa thu hoặc từ mốc xác định
-> Nhận dữ liệu
-> Kiểm schema, định danh, timestamp, đơn vị, chất lượng
-> Lưu bền vững tại gateway
-> Xác nhận đã nhận với sensor
```

Chỉ khớp `node_id` không đủ để xác thực. Gateway cần bản sao thông tin đăng ký hợp lệ để làm việc offline; tài liệu phải nêu thời hạn cache và giới hạn nhận biết thu hồi khi mất mạng.

Phân biệt ba loại kết quả:

- Dữ liệu hợp lệ, bình thường: lưu và xử lý.
- Dữ liệu hợp lệ nhưng vượt ngưỡng nghiệp vụ: vẫn lưu để tạo cảnh báo.
- Dữ liệu sai định dạng hoặc không hợp lệ về kỹ thuật: ghi nhận lỗi/cách ly, không đưa vào biểu đồ như bản đo tốt.

Phát hiện thiếu dữ liệu dựa trên sequence gap hoặc chu kỳ đo đã cấu hình. Timestamp đơn lẻ không đủ kết luận mất bản ghi khi chưa biết tần suất đo.

Kiểm thử: node lạ; xác thực sai; timeout; payload thiếu trường; sai đơn vị; sequence bị hổng; bản ghi trùng; giá trị vượt ngưỡng vẫn được lưu; gateway tắt sau khi nhận.

Đầu ra: collection engine, chương trình node, simulator và log thu thập có thể đối chiếu.

## 10. Lưu offline và đồng bộ chống mất/trùng dữ liệu

Yêu cầu: F20 và các yêu cầu phi chức năng liên quan.

Trạng thái hàng đợi đề xuất: `pending -> sending -> acknowledged`; lỗi tạm thời về `pending` với lịch retry; lỗi dữ liệu vĩnh viễn chuyển khu vực cách ly để xử lý. Khi khởi động lại, phải phục hồi được các bản ghi đang `sending`.

Cơ chế:

1. Sensor giữ bản đo đến khi gateway xác nhận đã lưu bền vững.
2. Gateway ghi dữ liệu và trạng thái hàng đợi trong giao dịch cục bộ.
3. Có mạng thì gửi batch có định danh; lỗi kết nối retry với khoảng chờ tăng dần và giới hạn phù hợp.
4. Server xác thực gateway, kiểm phạm vi node/farm, kiểm payload và ghi dữ liệu bằng giao dịch.
5. Khóa duy nhất ở database đảm bảo gửi lại cùng bản đo không tạo bản ghi mới. Nếu cùng ID nhưng nội dung khác phải báo xung đột, không âm thầm ghi đè.
6. Server chỉ ACK sau khi commit; trả kết quả riêng từng bản ghi: đã nhận, đã tồn tại, không hợp lệ hoặc cần gửi lại.
7. Gateway đánh dấu đã xác nhận theo ACK; chỉ dọn dữ liệu theo chính sách lưu giữ. Không xóa cả batch khi có lỗi một phần.
8. Cảnh báo khi dung lượng cục bộ gần đầy; xác định sức chứa theo tần suất đo và thời gian mất mạng dự kiến.

Trường hợp bắt buộc demo: server lưu thành công nhưng ACK bị mất. Gateway gửi lại và database vẫn chỉ có một bản đo; cảnh báo/thông báo cũng không bị tạo trùng do ingest lại.

Kiểm thử: mất mạng; mạng chập chờn; reboot gateway; server lỗi trước/sau commit; batch có bản ghi lỗi; gửi lại batch; hết dung lượng; đồng hồ sensor sai.

Đầu ra: pipeline offline/sync và bằng chứng đối chiếu số bản đo tại sensor, gateway, server. Không tuyên bố “không mất dữ liệu tuyệt đối” khi thiết bị hỏng hoặc vượt dung lượng thiết kế.

## 11. Dashboard, bản đồ, lịch sử và so sánh zone

Yêu cầu: F04–F07.

Cơ chế:

- Dashboard tổng hợp thiết bị, cảnh báo mở, mission và thời điểm cập nhật.
- Bản đồ farm/zone/node; tọa độ UAV chỉ hiển thị khi có nguồn hợp lệ.
- Bản đo mới nhất theo thời điểm đo, kèm đơn vị, chất lượng và độ cũ.
- Biểu đồ lịch sử có bộ lọc sensor, loại đại lượng, khoảng thời gian và zone.
- So sánh zone theo cùng đại lượng/đơn vị, khoảng thời gian và cách tổng hợp; thể hiện số mẫu và khoảng thiếu dữ liệu.
- Dữ liệu lịch sử đồng bộ muộn được chèn đúng trục thời gian, không làm lùi giá trị mới nhất đang hiển thị.

Kiểm thử: farm chưa có dữ liệu; tọa độ thiếu; bản ghi đến muộn; hai zone thiếu mẫu khác nhau; bộ lọc theo quyền; cập nhật dashboard trong khoảng 10 giây sau đồng bộ.

Đầu ra: các màn hình sử dụng API thật và minh chứng dữ liệu khớp database.

## 12. Cấu hình ngưỡng, cảnh báo và thông báo

Yêu cầu: F08–F10.

Ba nhóm phát hiện:

- Ngưỡng giá trị: trên/dưới giới hạn của từng loại cảm biến.
- Data timeout: quá thời gian chưa có dữ liệu mới; cần tác vụ định kỳ vì không có bản ghi mới để kích hoạt kiểm tra.
- Pin yếu: theo thiết bị có dữ liệu pin. Pin không được cung cấp phải hiển thị “không xác định”, không coi bằng 0.

Luồng xử lý:

```text
Cấu hình rule có phạm vi và đơn vị
-> Nhận dữ liệu mới hoặc chạy kiểm tra định kỳ
-> Đánh giá điều kiện
-> Tạo/cập nhật alert
-> Tạo thông báo web và yêu cầu gửi email
-> Admin xem, ghi nhận xử lý và đóng alert
-> Lưu lịch sử người xử lý, thời gian, nội dung
```

Trạng thái đề xuất: `open -> acknowledged -> resolved/closed`, thống nhất ý nghĩa “điều kiện đã hết” và “người dùng đóng”. Áp dụng quy tắc gộp sự kiện/cooldown để cùng một tình trạng kéo dài không tạo vô hạn alert và email.

Cần tách thời điểm sự kiện và thời điểm phát hiện. Với dữ liệu cũ đồng bộ muộn, ghi rõ là bất thường trong quá khứ và áp dụng chính sách thông báo đã cấu hình. Timeout phải phù hợp chu kỳ UAV thu thập; backend không đủ bằng chứng để khẳng định sensor hỏng chỉ vì chưa nhận dữ liệu.

Thông báo mission: phát sinh từ mission thất bại/thu thất bại hoặc điều kiện vận hành được thống nhất; không chỉ triển khai email cho alert môi trường. Gửi email thất bại phải retry và có trạng thái giao nhận.

Kiểm thử: đúng bằng ngưỡng; vượt ngưỡng; quá hạn không có dữ liệu; pin yếu; thiếu pin; dữ liệu cũ; email lỗi; gửi lại batch; đóng alert; sự cố mission.

Đầu ra: cấu hình rule, danh sách/lịch sử alert, trung tâm thông báo và minh chứng nhận email thử nghiệm.

## 13. Báo cáo và truy vết

Yêu cầu: F11 và hỗ trợ F17.

| Báo cáo | Nội dung |
|---|---|
| Sensor | Diễn biến, min/max/trung bình theo đại lượng, số mẫu và khoảng thiếu dữ liệu |
| Device | Phân bố theo farm/zone, trạng thái, pin, thời điểm liên lạc |
| Alert | Loại, mức độ, trạng thái, thời gian phát hiện/xử lý, người xử lý |
| UAV mission | Tuyến, target, điểm thành công/thất bại, lý do lỗi, số bản ghi thu và trạng thái sync |

Chốt định nghĩa “tỷ lệ thành công”: ví dụ số target thu thành công chia số target dự kiến, kèm cách xử lý nhiệm vụ bị hủy và target thử lại. Không tính số lần retry thành số target mới.

Định dạng xuất và bộ lọc báo cáo cần thống nhất; đề yêu cầu tạo báo cáo nhưng không bắt buộc một định dạng file cụ thể.

Kiểm thử: đối chiếu dữ liệu gốc; khoảng thời gian; phạm vi quyền; báo cáo không có dữ liệu; số liệu mission thu xong nhưng chưa sync.

Đầu ra: báo cáo mẫu có thể tái tạo và truy ngược về bản ghi/mission tương ứng.

## 14. Bảng ánh xạ yêu cầu – triển khai – bằng chứng

Đây là bảng tương ứng với bảng ánh xạ X.800 của workflow cũ. Với đề này, trục ánh xạ là yêu cầu F01–F20 và minh chứng đáp ứng.

| Mã | Nội dung | Phần workflow | Testcase/bằng chứng cần có |
|---|---|---|---|
| F01 | Login/logout Admin | 6 | TC-F01: login đúng/sai, phiên hết hiệu lực sau logout |
| F02 | Quản lý tài khoản | 6 | TC-F02: tạo/sửa/khóa tài khoản, chặn trái quyền |
| F03 | Farm và zone | 6 | TC-F03: quản lý, quan hệ và phạm vi quyền |
| F04 | Dashboard tổng quan | 11 | TC-F04: số thiết bị/alert/mission khớp dữ liệu |
| F05 | Bản đồ sensor | 11 | TC-F05: vị trí, trạng thái và thiếu tọa độ |
| F06 | Hiện tại và lịch sử | 11 | TC-F06: lọc thời gian, đơn vị, dữ liệu đến muộn |
| F07 | So sánh zone | 11 | TC-F07: cùng đại lượng và cửa sổ thời gian |
| F08 | Xử lý/đóng alert | 12 | TC-F08: ghi chú, người xử lý, lịch sử trạng thái |
| F09 | Ngưỡng, timeout, pin | 12 | TC-F09: ba nhóm rule và các giá trị biên |
| F10 | Web/email notification | 12 | TC-F10: alert, sự cố mission, email lỗi và retry |
| F11 | Báo cáo | 13 | TC-F11: đủ sensor/device/alert/mission, số liệu đúng |
| F12 | Đăng ký/gán sensor | 7 | TC-F12: node trùng, chuyển zone, giữ lịch sử |
| F13 | UAV/gateway | 7 | TC-F13: quản lý và lịch sử gắn gateway |
| F14 | Tạo mission | 8 | TC-F14: farm, target, tuyến/waypoint hợp lệ |
| F15 | Theo dõi telemetry | 8 | TC-F15: vị trí/pin/waypoint/tiến độ, telemetry cũ |
| F16 | Trạng thái và ghi chú | 8 | TC-F16: cập nhật tự động/thủ công, xung đột dữ liệu |
| F17 | Kết quả mission | 8, 13 | TC-F17: điểm thành công/thất bại, lỗi và retry |
| F18 | Discover/authenticate | 9 | TC-F18: thiết bị đăng ký, node lạ, xác thực sai |
| F19 | Collect/validate | 9 | TC-F19: thiếu, trùng, sai và vượt ngưỡng nghiệp vụ |
| F20 | Offline và auto sync | 10 | TC-F20: mất mạng, reboot, mất ACK, chống trùng |

Mỗi mã TC là một nhóm testcase; khi triển khai tách thành ca cụ thể với tiền điều kiện, thao tác, kết quả mong muốn, kết quả thực tế và đường dẫn bằng chứng.

## 15. Kiểm thử tổng hợp và yêu cầu phi chức năng

| Yêu cầu từ đề | Cách kiểm/đo | Điều kiện cần ghi trong báo cáo |
|---|---|---|
| Uptime ít nhất 99% trong điều kiện bình thường | Theo dõi endpoint và chức năng cốt lõi, ghi downtime; uptime = thời gian sẵn sàng / tổng thời gian quan sát | Chốt cửa sổ đo, điều kiện bình thường, lịch bảo trì và cách đo; demo ngắn không đủ chứng minh độ sẵn sàng dài hạn |
| Web thường đáp ứng khoảng 3 giây | Đo các thao tác đại diện với bộ dữ liệu và tải đồng thời đã chốt | Ghi cấu hình, số user/sensor/bản ghi, mạng, số lần đo và phân vị; p95 là tiêu chí nghiệm thu đề xuất cần thống nhất |
| Dữ liệu vừa sync lên dashboard khoảng 10 giây | Gắn ID bản ghi, đo từ lúc backend chấp nhận/lưu đến lúc UI hiển thị | Định nghĩa mốc bắt đầu/kết thúc; không bao gồm thời gian chờ UAV hoặc chờ mạng |
| Lưu trữ an toàn, truy cập có xác thực | Kiểm quyền người dùng/thiết bị, bảo vệ bí mật, kênh truyền và dữ liệu lưu | Kiểm cả API ingest và API web; chọn cơ chế bảo vệ phù hợp phần cứng |
| Backup định kỳ | Lập lịch, tạo backup, khôi phục sang môi trường thử và đối chiếu | Chốt chu kỳ, thời gian giữ, mục tiêu mất dữ liệu và thời gian phục hồi |
| Offline/retry/chống trùng | Ngắt mạng, restart, làm mất ACK, gửi lại và đếm bản ghi | Ghi thời gian offline, dung lượng, số batch và bản ghi lỗi |
| Modular và mở rộng | Thêm farm/zone/node/UAV/gateway, thêm loại sensor theo hợp đồng đã thiết kế | Chứng minh mở rộng qua cấu hình/dữ liệu hoặc adapter, nêu giới hạn còn lại |
| Chrome, Edge, Firefox | Chạy bộ luồng chính trên cả ba trình duyệt | Ghi phiên bản thực tế và ảnh/kết quả; không suy từ một trình duyệt |

Bộ kịch bản tích hợp trọng tâm:

1. Luồng bình thường: tạo farm/node/mission → thu → sync → xem dữ liệu → xuất báo cáo.
2. Offline: thu khi mất Internet → restart gateway → nối mạng → tự sync đủ.
3. Mất ACK: server đã commit → gửi lại → không trùng dữ liệu hoặc cảnh báo.
4. Lỗi một phần: một node không phản hồi và một payload sai → các bản hợp lệ vẫn đi tiếp; mission có lý do lỗi.
5. Cảnh báo: vượt ngưỡng, pin yếu, timeout → web/email → ghi nhận xử lý → đóng.
6. Telemetry: có dữ liệu → mất kết nối → hiển thị độ cũ → Operator cập nhật ghi chú/trạng thái.
7. Truy cập: người dùng sai vai trò/farm và gateway sai phạm vi bị chặn.
8. Khôi phục: cài từ gói triển khai, restore backup và mở được dữ liệu kiểm chứng.

Mẫu bảng kết quả:

| Test ID | Yêu cầu | Tiền điều kiện/thao tác | Mong muốn | Thực tế | Pass/Fail | Bằng chứng |
|---|---|---|---|---|---|---|
| TC-F20-ACK | F20 | Server commit, làm mất ACK, gateway gửi lại | Một bản đo duy nhất, nhận lại ACK | Chưa chạy | Chưa đánh giá | Chưa có |

## 16. Chia gói công việc và sprint Agile/Scrum

Ánh xạ đúng năm task package của đề:

| Gói | Nội dung | Phụ thuộc/đầu ra |
|---|---|---|
| TP1 | Phân tích, UML, CSDL, API, giao tiếp thiết bị | Chốt contract và tiêu chí nghiệm thu cho TP2–TP4 |
| TP2 | Backend, ingestion, validation, storage | Có API kiểm thử, xử lý offline/sync cùng TP4 |
| TP3 | Web Admin/Operator | Dựng giao diện bằng contract, sau đó tích hợp API thật |
| TP4 | Sensor, gateway, mission monitoring | Làm thử phần cứng sớm, tích hợp với TP2/TP3 |
| TP5 | Triển khai, kiểm thử, đánh giá, tài liệu | Thu bằng chứng xuyên suốt; hoàn thiện bàn giao cuối |

Lịch tham khảo 6 sprint, mỗi sprint 1–2 tuần; đây là giả định lập kế hoạch, cần điều chỉnh theo hạn nộp và số thành viên:

| Sprint | Mục tiêu | Điều kiện hoàn thành |
|---|---|---|
| 1 | Phân tích F01–F20, quyền, UML/ERD ban đầu; thử node–gateway | Contract v1 và chứng minh giao tiếp tối thiểu với thiết bị có sẵn |
| 2 | Tài khoản, farm/zone, thiết bị; luồng một bản đo tới web | API nền tảng và dữ liệu hiển thị qua pipeline thật hoặc simulator có ghi nhãn |
| 3 | Thu thập xác thực, validation, offline/sync/chống trùng | Mất mạng, restart, mất ACK không làm trùng/mất bản ghi trong phạm vi thử |
| 4 | Mission, waypoint, kết quả, telemetry và cập nhật tay | Nhiệm vụ hoàn chỉnh, có thất bại một phần và mất telemetry |
| 5 | Dashboard, lịch sử/so sánh, cảnh báo, email, báo cáo | F04–F11 chạy trên dữ liệu đã thu/sync |
| 6 | Tích hợp prototype, kiểm NFR, restore, đóng gói và demo | Ma trận truy vết đủ bằng chứng; công khai các mục chưa đạt |

Trong mỗi sprint: lập kế hoạch → chia task theo người phụ trách → cập nhật tiến độ/ngăn trở → review sản phẩm chạy được → retrospective → cập nhật backlog và tài liệu.

Phân công theo mảng: phân tích/tích hợp; backend/database; frontend; sensor/gateway; kiểm thử/tài liệu. Một người có thể kiêm nhiều mảng; nhóm nhỏ vẫn cần người khác kiểm tra phần đã làm. Đây là phương án chia việc, chưa gán tên thành viên.

Ưu tiên kiểm chứng đường đi sensor → gateway offline → server → web từ sớm; không chờ hoàn thiện toàn bộ giao diện mới thử phần cứng và mất mạng.

## 17. Kịch bản demo và bộ hồ sơ nộp

Bộ dữ liệu demo đề xuất: một farm, hai zone, ba node, một UAV/gateway, hai tài khoản theo vai trò và ít nhất một mission. Số lượng này phục vụ minh họa, không phải chỉ tiêu bắt buộc trong đề.

Thứ tự demo:

1. Admin đăng nhập, xem farm/zone và phân quyền.
2. Operator xem node/UAV/gateway, tạo mission và tuyến waypoint.
3. Gateway nhận dữ liệu hợp lệ và từ chối một thiết bị không được xác thực.
4. Ngắt Internet, thu bản đo mới và xem hàng đợi offline.
5. Khởi động lại gateway, chứng minh dữ liệu còn; nối mạng để tự sync.
6. Gửi lại dữ liệu đã nhận, đối chiếu không sinh bản đo trùng.
7. Dashboard, bản đồ, lịch sử và so sánh zone hiển thị dữ liệu.
8. Kích hoạt rule thử nghiệm, nhận thông báo web/email, xử lý và đóng alert.
9. Cho thấy telemetry khi có nguồn, tình trạng thiếu telemetry và cập nhật tay.
10. Xem điểm mission thành công/thất bại, xuất báo cáo và trình bày kết quả kiểm thử.

Giá trị giả lập phải được gắn nhãn. Lưu log, thời gian và ID bản ghi để ảnh chụp có thể đối chiếu với database. Không đánh dấu Pass cho ca chưa chạy.

Checklist bàn giao:

- [ ] SRS, use case, ma trận quyền, backlog và lịch sử sprint.
- [ ] Đủ sáu loại UML 2.0 trong đề, ERD và từ điển dữ liệu.
- [ ] API contract, giao thức sensor–gateway, thiết kế offline/sync.
- [ ] Source backend, frontend, gateway, firmware và simulator nếu có.
- [ ] Prototype sensor và gateway gắn trên UAV; ghi rõ phần đã kiểm chứng thực tế.
- [ ] Migration/seed, cấu hình mẫu, gói triển khai và hướng dẫn cài đặt.
- [ ] Test plan, testcase, kết quả thực tế, bằng chứng F01–F20/NFR.
- [ ] Hướng dẫn dùng cho Admin/Operator; vận hành gateway và xử lý sync lỗi.
- [ ] Hướng dẫn backup/restore và kết quả phục hồi thử.
- [ ] Báo cáo phân tích thiết kế, đánh giá, giới hạn và hướng phát triển.
- [ ] Slide và kịch bản demo có thể chạy lại.

## 18. Thứ tự thực hiện toàn bài

```text
Đọc đề, chốt phạm vi và điều kiện phần cứng
-> Phân tích F01–F20, NFR và quyền truy cập
-> UML + ERD + API contract + giao thức thiết bị
-> Dựng môi trường, dữ liệu mẫu và pipeline tối thiểu
-> Tài khoản + farm/zone + quản lý thiết bị
-> Xác thực sensor + thu thập + validation
-> Offline + retry + ACK + chống trùng
-> Mission + waypoint + telemetry/cập nhật tay + kết quả
-> Dashboard + bản đồ + lịch sử + so sánh zone
-> Ngưỡng + timeout + pin yếu + web/email
-> Báo cáo + truy vết
-> Tích hợp phần cứng + kiểm thử chức năng/NFR + thu bằng chứng
-> Cài đặt lại + backup/restore + đóng gói
-> Hoàn thiện tài liệu + demo + đối chiếu yêu cầu nghiệm thu
```

Các quyết định cần ghi nhận trước khi bắt đầu triển khai: hạn nộp, thành viên, phần cứng thực có, mức demo UAV được yêu cầu, giao thức và vùng phủ thử nghiệm, nguồn telemetry, tải dự kiến, thời gian offline cần chịu được, chính sách dữ liệu cũ và tiêu chí đo NFR. Những thông tin này chưa có trong file đề nên chưa thể coi là đã chốt.
