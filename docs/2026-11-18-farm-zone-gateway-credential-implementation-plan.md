# HQTCSDL — Thay đổi Farm/Zone, Gateway Credential và kế hoạch triển khai sau PR #14

> **Tài liệu tổng hợp cho đợt triển khai tiếp theo — 18/11/2026**  
> **Phạm vi:** Review R1–R6; FL-01/02/03/04/06/07; schema Farm/Zone; Gateway Credential; simulator; mốc MVP gửi–lưu–xem.  
> **Nguồn thiết kế:** Confluence HQTCSDL cập nhật ngày 18/11/2026. **Nguồn code được đối chiếu:** nhánh `develop`, commit `a846ac1d21d2adeef0a9f6a31553b25b7c2fa869` (merge PR #14).  
> **Trạng thái:** Quyết định nghiệp vụ và đặc tả mới đã được ghi ở Confluence; **code, migration, SQL tests và E2E cho phạm vi mới chưa triển khai/chưa có bằng chứng hoàn tất**.  
> **Loại tài liệu:** Kế hoạch delta implementation, không thay thế đặc tả chi tiết FL hoặc Integration Contract.

## 1. Mục tiêu và kết luận rà soát

Sau khi cập nhật yêu cầu, HQTCSDL chuyển từ mô hình `area_manifest` chọn một Farm và hai Zone cố định sang **quản lý Farm/Zone động bằng SQL Server và giao diện Admin**. Demo vẫn là **1 Farm, 2 Zone (trong nhà/ngoài trời), 2 Node và 1 Gateway**; số lượng demo không giới hạn cardinality của schema/API.

Cùng đợt thay đổi, Admin có thể **quản lý vòng đời API key Gateway trên web**: cấp mới, xem trạng thái/lịch sử, thay khóa (rotation), thu hồi và khôi phục khi mất response. Cấu trúc `GatewayCredential` hiện có phù hợp và không cần bảng audit riêng.

**Kết luận kỹ thuật:** Không viết lại toàn hệ thống. Giữ 19 bảng trong thiết kế CSDL đầy đủ (source `develop` hiện có 15 bảng M1/M2); cần migration bổ sung **4 cột Farm/Zone**, thay các validator/caller đang phụ thuộc manifest, triển khai chức năng mới theo PR nhỏ, test SQL thật và bảo vệ ingestion/ACK2.

### 1.1. Đã chốt / giữ nguyên / chưa làm

| Nhóm | Quyết định hiện hành |
| --- | --- |
| Khu vực | SQL Server là nguồn sự thật của Farm/Zone; bỏ `area_manifest` **khu vực**; không dùng `TOP 1` tự chọn Farm |
| Số lượng | Nhiều Farm, mỗi Farm nhiều Zone; demo 1 Farm/2 Zone; code không hardcode |
| Farm/Zone | Admin tạo, danh sách, chi tiết, sửa **name/description**, archive và restore; code/parent FK bất biến; không DELETE/cascade |
| Admin/Auth | Một tài khoản Admin tạo sẵn; giữ login/session/cookie/CSRF/2 giờ; không mở nhiều vai trò, reset-password/đăng ký mới |
| Node/Gateway | Giữ phần hồ sơ đã merge, điều chỉnh chọn Farm/Zone từ DB; Node–Gateway pairing vẫn là provisioning kỹ thuật cố định, **chưa có web pairing/chuyển vùng** |
| Credential | Cấp ngẫu nhiên trên Backend, lưu SHA-256 hash, secret hiển thị một lần; tối đa **2 khóa active/Gateway**, soft revoke bằng `revoked_at`, giữ lịch sử đơn giản |
| MetricDefinition | Giữ 5 metric seed và miền kỹ thuật ổn định; có thể xem nhưng không thêm/sửa danh mục từ web |
| Ingestion | Giữ `POST /api/v1/ingestion/measurements`, `X-API-Key`, identity `(device_id,boot_id,seq)`, canonicalization, batch 1–50, per-record ACK2 |
| Cảnh báo | Không tự sửa period/L/S/checkpoint/replay khi archive/restore; FL-04 sở hữu thay đổi quyền tiếp nhận và hiệu ứng giám sát |
| Môi trường | HTTP local/LAN được chấp nhận cho demo; không coi API key an toàn trên mạng không tin cậy; triển khai public cần HTTPS |

### 1.2. Những giới hạn cũ bị thay thế

1. `area_manifest={farm_code,zone_codes:{indoor,outdoor}}` không còn là nguồn chọn Farm/Zone runtime.
2. FL-02 không còn chỉ đọc Farm/Zone seed; Admin có quyền quản lý vòng đời khu vực.
3. Đăng ký Gateway không tự lấy Farm từ manifest; request cần `farm_id` rõ ràng.
4. FE không mặc định dùng `zones[0]` như một lựa chọn nghiệp vụ.
5. Gateway API key không còn chỉ được cấp bằng CLI riêng; Admin quản lý qua Gateway Detail.
6. Số lượng một Farm/hai Zone là **fixture demo**, không phải constraint áp vào SQL.

**Không bỏ:** seed demo tùy chọn, các mã ID/FK cũ, tính bất biến của nguồn dữ liệu, provenance của Node/Gateway, ACK1/ACK2, phân biệt Event Time–Persisted At, quy tắc chống trùng và các hợp đồng đã chốt.

## 2. Đối chiếu GitHub `develop` tại thời điểm lập kế hoạch

- Repo: https://github.com/nhanhehehe/HQTCSDL
- PR mới nhất đã merge vào `develop`: **PR #14 — Feat/4 context device profiles**, merged ngày 17/11/2026: https://github.com/nhanhehehe/HQTCSDL/pull/14
- Merge commit: `a846ac1d21d2adeef0a9f6a31553b25b7c2fa869`; đối chiếu `develop` với commit này trả **identical**.
- PR #13 đã mang Auth/session/CSRF + M2; PR #14 triển khai **GET /context theo manifest**, 8 API hồ sơ Node/Gateway, exact-code lookup, HMAC cursor, rowversion và atomic create Node+NodeRuntime+EvaluationState.
- PR #14 ghi nhận **76 Backend tests passed; 2 SQL opt-in skipped; 16 Frontend tests passed**. Đây là kết quả trong mô tả PR, **không phải kiểm thử mới chạy trong đợt rà soát tài liệu**.
- Các ca SQL thực tế chưa có evidence hoàn tất theo PR #14: lỗi tạo runtime/state rollback, hai POST đồng thời cùng mã, logout/write race, snapshot/pool, lost response mutation; cần giữ trong regression.
- Source hiện **chưa có** Farm/Zone CRUD/archiving, web credential provisioning, ingestion/ACK2, API đo current/history/chart, simulator tích hợp, M3 và worker.

### 2.1. File hiện hành và delta dự kiến

| File/module | Đang có trên `develop` | Thay đổi cần làm |
| --- | --- | --- |
| `backend/migrations/versions/a70b8056608d_create_m1_foundation_tables.py` | Farm/Zone chưa có archive/rowversion; đã có GatewayCredential | **Không sửa revision cũ**; tạo revision Alembic mới |
| `backend/migrations/versions/b15e2c03a001_create_m2_tables.py` | M2, NodeRuntime/EvaluationState; head PR #14 | Giữ nguyên; đối chiếu `alembic heads/current` trước migration |
| `backend/app/core/config.py` | `area_manifest` trong Settings | Loại bỏ sau khi migrate caller sang DB |
| `backend/app/modules/devices/context.py` | `parse_manifest`, `read_context` theo code cố định | Thay bằng DB target/query; legacy `/context` adapter |
| `backend/app/modules/devices/service.py` | `self.manifest`, create Gateway suy ra Farm; create Node lấy Zone từ context | Guard Farm/Zone từ DB; gateway create nhận `farm_id`; giữ transaction session/CSRF |
| `backend/app/modules/devices/schemas.py` | `GatewayCreate` chỉ `gateway_id,name,notes` | Thêm `farm_id`, DTO Farm/Zone và version; giữ quy ước ID string |
| `backend/app/modules/devices/router.py` | `/context` và 8 API profile | API Farm/Zone và credential; tách router/service nếu cần, tránh router phình to |
| `backend/app/modules/devices/repository.py` | Repo hồ sơ có rowversion/lookup | Thêm repo khu vực/credential và xác minh parent, order locks |
| `frontend/src/features/devices/DevicesPanel.tsx` | Đọc `/context`, mặc định Zone đầu, tạo Gateway không farm_id | UI chọn Farm/Zone rõ ràng; bổ sung Gateway Credential tab |
| `backend/.env.example`, `README.md`, `docs/development.md` | Còn `HQ_AREA_MANIFEST` và quy trình seed/context cũ | Cập nhật **trong PR code chuyển đổi**, không để hướng dẫn lạc hậu |
| `backend/app/main.py` | Include router auth/devices | Bổ sung router mới hoặc đăng ký trong devices; giữ error/no-store |

**Chỉ xóa `HQ_AREA_MANIFEST` sau khi đã bỏ phụ thuộc ở Backend/FE/tests/README; tuyệt đối không xóa ngay trong PR migration.**

## 3. Thiết kế CSDL mới và migration

### 3.1. Thay đổi duy nhất ở schema cho review này

| Bảng | Cột mới | SQL Server | Hành vi |
| --- | --- | --- | --- |
| `dbo.Farm` | `archived_at` | `DATETIME2(3) NULL` | `NULL` nghĩa là đang dùng; timestamp UTC khi archive |
| `dbo.Farm` | `row_ver` | `ROWVERSION NOT NULL` | 8 byte optimistic concurrency, DTO base64 chuẩn |
| `dbo.Zone` | `archived_at` | `DATETIME2(3) NULL` | Như Farm |
| `dbo.Zone` | `row_ver` | `ROWVERSION NOT NULL` | Như Farm |

Các bảng `Gateway`, `Node`, `GatewayCredential`, `MetricDefinition`, `Measurement`, `MeasurementValue`, `NodeRuntime`, `EvaluationState` không cần đổi cột vì riêng đợt này. Giữ FK `Farm→Zone`, `Farm→Gateway`, `Zone→Node`, `Gateway→Node`.

- Dữ liệu hiện có: `archived_at=NULL`, SQL Server tự sinh `ROWVERSION`; bảo toàn ID, mã, FK và các bản đo/runtime.
- Tạo revision Alembic sau **head thực tế** (PR #14 báo `b15e2c03a001`; phải đối chiếu lại trước khi tạo).
- Áp migration trên `HQTCSDL_test_nhan` **trước** dev, sử dụng URL migration và xác nhận `DB_NAME()`, quyền app/migration; không stamp, `IDENTITY_INSERT`, xóa DB hoặc sửa M1/M2 để né lỗi.
- Không bắt buộc downgrade dữ liệu lifecycle đã được dùng; đánh giá rủi ro trước nếu cần rollback.
- `row_ver` tự thay khi cập nhật thực; request stale phải 409 ngay cả nếu giá trị gửi giống dữ liệu đang có. No-op với version hiện hành giữ nguyên version/timestamp.
- Không thêm bảng audit nhiều Admin. Lịch sử API key dựa trên các dòng credential với `created_at`/`revoked_at`.

### 3.2. Quy tắc bất biến và archive/restore

- `farm_code`, `zone_code` so exact bằng BIN2; giữ nguyên sau tạo. `Zone.farm_id`, `Node.zone_id`, `Gateway.farm_id` không được sửa.
- Archive Zone **chỉ khi mọi Node của Zone đã `receiving_enabled=false`** qua FL-04.
- Archive Farm **chỉ khi mọi Zone đã archive và mọi Gateway thuộc Farm `receiving_enabled=false`**.
- Không tự tắt hàng loạt; không cascade, không hard delete và không thay đổi nguồn lịch sử.
- Restore Farm trước; restore Zone chỉ khi Farm active; restore không tự bật Gateway/Node, không tái tạo monitoring periods hay reset S/L/generation.
- Chỉ cho tạo Zone/Gateway dưới Farm active; tạo Node dưới Zone+Farm active; enable quyền phải recheck cha active.
- Khi có thiết bị bật quyền làm archive không hợp lệ, trả **409 `AREA_IN_USE`**; không chặn việc đọc lịch sử.

### 3.3. Khóa giao dịch và đồng thời

Thứ tự chuẩn khi có liên quan:

```text
Session (API web) -> Farm -> Zone (ID tang dan) -> Gateway (ID tang dan)
                  -> Node (ID tang dan) -> Runtime/State/Episode
```

Ingestion **không dùng session web**, nhưng vẫn giữ thứ tự Farm→Zone→Gateway→Node ở phần thao tác chung. Phải resolve ID trước khi lấy khóa cha, sau đó re-read liên kết bất biến và kiểm nguồn/credential trước commit. Không lấy lại Farm/Zone sau khi đã giữ Gateway/Node.

- Archive cạnh tranh với CREATE Zone/Node/Gateway, RESTORE hoặc ENABLE phải dùng **cùng parent guard** để tránh phantom.
- Issue/revoke credential dùng Session→Farm→Gateway; quota hai active được kiểm tra trong transaction, **không tin count FE**.
- GET sử dụng SNAPSHOT cho tập dữ liệu nhất quán; không giữ writer lock trong lúc người dùng xem web.
- Cần thử **SQL hai kết nối** cho các race; test mock không thay thế bằng chứng SQL.

## 4. FL-02 mới — API và DTO quản lý Farm/Zone

### 4.1. API dự kiến (theo Confluence FL-02)

| Method | Endpoint | Kết quả và input chính |
| --- | --- | --- |
| GET | `/api/v1/farms` | `status=ACTIVE|ARCHIVED|ALL` (mặc định ACTIVE), exact `code`, `page_size/cursor`; danh sách |
| POST | `/api/v1/farms` | `{code,name,description?}`; 201 và Location |
| GET | `/api/v1/farms/{id}` | Chi tiết Farm |
| PATCH | `/api/v1/farms/{id}` | `{name?,description?,expected_row_version}` |
| POST | `/api/v1/farms/{id}/archive` | `{expected_row_version}`; check các Zone/Gateway |
| POST | `/api/v1/farms/{id}/restore` | `{expected_row_version}` |
| GET | `/api/v1/farms/{id}/zones` | Danh sách Zone theo Farm, filter/pagination |
| POST | `/api/v1/farms/{id}/zones` | `{code,name,description?}`; Farm phải active |
| GET | `/api/v1/zones/{id}` | Chi tiết Zone |
| PATCH | `/api/v1/zones/{id}` | Metadata + expected version |
| POST | `/api/v1/zones/{id}/archive` | Version, mọi Node phải tắt quyền |
| POST | `/api/v1/zones/{id}/restore` | Version, Farm phải active |

`FarmView={id,code,name,description,archived_at,row_version}`; `ZoneView` thêm `farm_id`. IDs là **chuỗi thập phân**, `row_version` là base64 canonical của 8 byte, thời gian UTC. Code Farm/Zone theo Unicode NVARCHAR(128) và BIN2; không áp regex code thiết bị ASCII lên Farm/Zone.

List trả `{items,next_cursor}`, sort ID ASC, `page_size` mặc định 20, tối đa 100; cursor bind route, filter, `farm_id`, status, page_size, boundary. Mọi endpoint cần Admin session; mutation phải có Origin/Content-Type/CSRF theo FL-01. Web error envelope + `request_id` + no-store theo 03.5.1.

| Điều kiện | Status/mã |
| --- | --- |
| Input/body/query sai | 400 `INPUT_INVALID` |
| Chưa đăng nhập/session hết hạn | 401 `SESSION_INVALID` |
| Origin/CSRF sai | 403 `CSRF_INVALID` |
| Target không tồn tại | 404 `NOT_FOUND` |
| Mã trùng | 409 `CODE_EXISTS` |
| Version cũ | 409 `VERSION_CONFLICT` |
| Parent archived | 409 `AREA_ARCHIVED` |
| Archive còn ràng buộc hoạt động | 409 `AREA_IN_USE` |
| SQL không sẵn sàng | 503 `STORAGE_UNAVAILABLE` |

### 4.2. Chuyển đổi `GET /api/v1/context` có kiểm soát

Giữ adapter **read-only** với shape legacy `{farm,zones,configuration}` để không làm vỡ caller của PR #14:

- Mới: `GET /api/v1/context?farm_id={id}` lựa chọn Farm rõ bằng DB.
- Không truyền `farm_id`: 0 Farm active → `200 INVALID / AREA_NOT_CONFIGURED`; đúng 1 Farm active → dùng Farm đó; từ 2 Farm active → **409 `AREA_SELECTION_REQUIRED`**, không TOP1.
- Farm ID không tồn tại →404; Farm archived → trả Farm, zones rỗng, INVALID/AREA_ARCHIVED; Farm active nhưng 0 Zone vẫn là VALID trong adapter.
- Legacy FarmView/ZoneView **không thêm** `archived_at/row_version`; endpoint mới `/farms` và `/zones` trả DTO mở rộng.
- Lọc Zone active theo ID, không bắt buộc đúng hai Zone và không trả mã kỳ vọng của manifest.
- Các form mới chuyển sang list/detail API; chỉ xóa adapter khi **toàn bộ caller/tests đã chuyển**.

### 4.3. Frontend khu vực

Trang Farm/Zone: list/chi tiết/tạo/sửa/archive/restore, filter trạng thái, hiển thị empty/error. Khi archive bị chặn, chỉ rõ cần tắt quyền thiết bị qua FL-04; không lén tự tắt. Đổi Farm phải reset Zone selection, cursor/view token và bỏ response đã về muộn từ Farm cũ. Tên/mô tả được render thành text (không raw HTML). Mutation mất response: giữ input, đọc lại để đối chiếu, không tự ghi đè/version-retry.

Seed Farm/Zone demo chỉ còn là **tùy chọn explicit, idempotent**, chạy riêng trên DB được chỉ định, không chạy tự động lúc login/GET/startup; rerun không đổi ID/FK/metadata cũ hoặc tự restore.

## 5. FL-03 mới — Hồ sơ Gateway/Node và API key

### 5.1. Đăng ký hồ sơ

- `POST /api/v1/gateways` bổ sung `farm_id` trong body, ví dụ `{gateway_id:"GW-01",name:"Gateway chính",farm_id:"1"}`. Farm phải tồn tại và active; FK `Gateway.farm_id` vẫn bất biến sau tạo.
- `POST /api/v1/nodes` giữ `{device_id,name,zone_id,notes?}`; Zone và Farm cha phải active.
- Cho xem/sửa metadata hồ sơ ngay cả khi thiết bị chưa online hoặc chưa có measurement.
- `Node.gateway_id=NULL` vẫn có thể là hồ sơ hợp lệ nhưng **chưa đủ điều kiện ingestion**.
- Giữ atomic create Node+NodeRuntime+EvaluationState; rollback cả ba khi một insert lỗi; không seed alert/period giả.
- Giữ chính sách version conflict, exact code, session/CSRF, no-op, unknown outcome của PR #14.
- **Chưa thêm API web ghép/chuyển Node–Gateway**, provision cặp bằng quy trình kỹ thuật riêng, kiểm tra trước T10.

### 5.2. Gateway Credential Management (P0-1 đến P0-6)

| Thuộc tính | Quy tắc |
| --- | --- |
| Nơi cấp | Admin bấm cấp trong **Gateway Detail → API Credential**, không auto cấp lúc tạo Gateway |
| Sinh | FastAPI CSPRNG 32 byte; secret biểu diễn Base64URL không padding (43 ký tự) |
| Format | `hqgw_v1_` + 43 ký tự, tổng 51 ký tự |
| Lưu hash | `SHA-256(32 raw secret bytes)` → `dbo.GatewayCredential.key_hash BINARY(32) UNIQUE` |
| Lưu lịch sử | Mỗi lần cấp INSERT mới; `created_at` và `revoked_at`, **không DELETE** |
| Active | Tối đa 2 credential `revoked_at IS NULL`/Gateway; cho phép rotation A→B |
| Secret trả một lần | Chỉ trong **response POST sau commit**, `Cache-Control:no-store`; GET không trả secret hoặc hash |
| Revoke | UPDATE `revoked_at` UTC, idempotent, không tự đổi `receiving_enabled` |
| Mất response | Không tự retry POST; GET danh sách, reconcile, revoke credential không nhận secret rồi cấp lại |
| Farm archived | Không cấp mới; vẫn xem và revoke; nhận dữ liệu mới tuân FL-06 |
| Bảo vệ | Admin session/Origin/CSRF cho API web; Gateway ingestion dùng `X-API-Key` (không dùng browser cookie) |
| Audit | Chỉ lịch sử created/revoked trong bảng; không actor/bảng audit nhiều Admin |

API theo FL-03:

| Method | Endpoint | Đặc điểm |
| --- | --- | --- |
| GET | `/api/v1/gateways/{id}/credentials` | Phân trang mọi trạng thái, `credential_id DESC` |
| GET | `/api/v1/gateways/{id}/credentials/{credential_id}` | Credential thuộc đúng Gateway |
| POST | `/api/v1/gateways/{id}/credentials` | Body `{}`; 201 `{credential,api_key}`, trả secret một lần |
| POST | `/api/v1/gateways/{id}/credentials/{credential_id}/revoke` | Body `{}`; 200, revoke lặp giữ timestamp |

`CredentialView={id,gateway_id,created_at,revoked_at,status:ACTIVE|REVOKED}`; không có secret/hash. Quota đầy →409 `CREDENTIAL_LIMIT_REACHED`; sai Gateway/credential ở web →404. Các ID trong path là **ID số nội bộ**, khác mã wire `gateway_id` của Gateway.

Gateway khi gửi dữ liệu: header `X-API-Key: hqgw_v1_<secret>`. Backend kiểm format canonical, decode 32 bytes, SHA-256 trên **raw bytes**, tra `key_hash`, kiểm chưa revoke, đúng Gateway payload và đúng nguồn Node–Gateway; key sai/không tồn tại →401; key đã revoke/Gateway khác →403 theo 03.1.

Rotation: cấp B trong khi A active → cấu hình B vào Gateway/Simulator → xác minh B gửi thành công → revoke A. Khi logout, đóng modal, đổi Gateway, hết phiên thì xóa secret khỏi React memory; không đưa vào LocalStorage/SessionStorage/URL/log/telemetry. Sao chép qua trình duyệt cần fallback thủ công trên HTTP LAN.

## 6. FL-04 / FL-06 / FL-07 / Dashboard: tác động dây chuyền

### 6.1. FL-04 là điều kiện cho archive toàn phần

Hiện `develop` **chưa có FL-04 quyền nhận**. PR triển khai FL-02 có thể hoàn thành API archive với quy tắc 409 khi con còn bật, kiểm bằng SQL fixture, nhưng workflow Admin archive Zone/Farm có thiết bị đang bật **chưa hoàn chỉnh cho đến khi có FL-04**.

Không thêm API toggle đơn giản bỏ qua period/timeout/worker. Khi triển khai FL-04, phải giữ logic S/L, period, marker, timeout và trạng thái cảnh báo như Confluence, dùng cùng thứ tự guard cha.

### 6.2. FL-06 Ingestion — bất biến bắt buộc

Endpoint: `POST /api/v1/ingestion/measurements`. Request batch 1–50 records; identity `(device_id,boot_id,seq)`; năm metrics; `event_time` Node, `persisted_at` Backend. ACK2 per record: `STORED`, `ALREADY_EXISTS`, `RETRY_LATER`, `REJECTED`.

- **Xác thực credential và xác minh nguồn trước duplicate**. Revoke key không có ngoại lệ ACK bản trùng.
- Record **đã lưu** có cùng identity/canonical/source hợp lệ được `ALREADY_EXISTS` **trước** kiểm khu vực archived/receiving_enabled; không cập nhật metadata/L/desired_generation.
- Record **mới** ở khu vực archived/thiếu/không hợp lệ → `RETRY_LATER/CONFIGURATION_INVALID`, `ack=false`, Gateway giữ backlog; sai source vẫn xử lý INVALID_SOURCE; lỗi SQL STORAGE_UNAVAILABLE.
- Không thêm Farm/Zone vào payload wire; Backend truy từ Node→Zone→Farm và so với Gateway.farm_id.
- Với record mới, transaction ghi `Measurement`, **đúng 5 MeasurementValue**, cập nhật runtime/evaluation và marker theo thiết kế FL-06; chỉ trả STORED sau commit.
- Batch xử lý record riêng; revoke sau record A commit nhưng trước B: dừng phần còn lại theo request-level 403 khi phát hiện; gửi lại toàn batch bằng key hợp lệ → A có thể ALREADY_EXISTS, B xử lý mới.
- Tắt `receiving_enabled` **khác** revoke credential; không gộp lỗi quyền nhận thành 403 authentication.
- Không thay D06/canonical-v1, nguồn Event Time, chính sách duplicate/conflict, ACK2 và backlog đã chốt.

### 6.3. FL-07, dashboard, báo cáo

- Ba API số đo current/history/chart và query theo Node/Zone/Farm được lựa chọn rõ từ DB.
- Current theo Event Time, bảo toàn UNKNOWN Event Time và trạng thái lỗi cảm biến; không tự biến null thành 0.
- Cursor/view token phải gắn Node/range/filter/instance và phạm vi Farm khi áp dụng; đổi Farm reset trang, token và request generation.
- Dashboard/CSV chọn Farm rõ ràng, không dùng Farm đầu tiên/manifest; lịch sử của đối tượng archived vẫn đọc được theo quyền Admin.
- Không sửa replay/episode/timeout chỉ vì đổi tên hoặc archived khu vực.

## 7. Kế hoạch code — 7 PR nhỏ, có kiểm thử

**Không có PR nào dưới đây được tạo bởi tài liệu này.** Tên nhánh/PR chỉ là đề xuất; thứ tự phụ thuộc cần giữ. Mỗi PR cập nhật README/Confluence Chương 04/bằng chứng tương ứng khi triển khai.

| Thứ tự | Nhánh đề xuất | Phạm vi | Đạt khi |
| --- | --- | --- | --- |
| **A** | `feat/farm-zone-lifecycle-schema` | Revision Alembic thêm 4 cột Farm/Zone, kiểm DDL/upgrade | DB test giữ ID/FK/seed/raw/runtime; `archived_at NULL`, rowversion 8 bytes, head đúng |
| **B** | `feat/farm-zone-management` | Backend/FE FL-02, archive/restore, context adapter, target validators DB | F03-AC01–15, session/CSRF, cursor/versions, SQL race; 409 đúng với con còn bật |
| **C** | `feat/device-profiles-dynamic-area` | GatewayCreate `farm_id`, Node Zone động, FE chọn khu vực, regression PR #14, loại bỏ manifest sau chuyển đổi | Hồ sơ tạo/list/detail/PATCH ổn; Node+Runtime+State atomic; không tự chọn Farm |
| **D** | `feat/gateway-credential-management` | Credential list/detail/issue/revoke + Gateway Detail tab | F13-KAC01–07; secret một lần, quota 2, rollback/race, không log key |
| **E** | `feat/ingestion-ack2` | T09 canonicalization + T10 source/credential auth, SQL record Tx, ACK2 | FL-06 AC; trùng/xung đột, archive, source, rollback, revoked race; SQL thật |
| **F** | `feat/simulator-measurement-query` | T11 simulator batch/retry/offline fixture; T12 API current/history/chart | ACK runner, cursor/view consistency, UNKNOWN, dữ liệu đúng 5 metrics |
| **G** | `feat/measurement-dashboard-e2e` | T13 FE current/history/chart; T14 E2E demo | Admin login→chọn Farm/Node→Simulator→ingestion→SQL→biểu đồ; restart/backlog |

**Có thể song song:** T09 canonicalization pure functions và DTO/view fixtures sau khi hợp đồng API đã rõ. **Không gộp bừa:** migration với FE; credential rotation với thay đổi contract wire; toggle FL-04 với archive nếu chưa test alert/timeout.

### 7.1. Phụ thuộc và rào cản bắt buộc

| Phụ thuộc | Hướng xử lý |
| --- | --- |
| PR #14 đang theo manifest | PR B/C chuyển context/validators/FE theo thứ tự; không gỡ config trước khi caller hết dùng |
| M1/M2 trên DB thật | Revision mới, test DB test trước dev, kiểm `alembic current/heads` và DDL thật; không sửa history |
| FL-04 chưa có | Archive đang có Node/Gateway bật →409; hoàn chỉnh workflow khi quyền nhận được triển khai đúng design |
| `Node.gateway_id=NULL` | Provision pairing kỹ thuật có kiểm soát trước E2E; không ngầm link theo payload |
| M3/worker chưa có | Ghi `desired_generation` và checkpoint đúng contract; không giả đã chạy alert worker |
| 2 SQL tests skipped ở PR #14 | Thực thi SQL integration/race trên DB test, lưu bằng chứng, không suy test pass từ PR mô tả |
| HTTP local LAN | Dùng mạng demo tin cậy; key và payload không được TLS bảo vệ, production cần HTTPS |

### 7.2. Bước bắt đầu ngay: PR A

1. Đồng bộ local `develop` sạch từ `origin/develop`; kiểm head bằng `git log -1 --oneline`.
2. Kiểm `backend/.env` đang trỏ **test database phù hợp** qua `HQ_DATABASE_URL` và `HQ_MIGRATION_DATABASE_URL`; không ghi secret ra log/commit.
3. Kiểm `uv run alembic heads`, `uv run alembic current`, `DB_NAME()`, bảng `Farm`/`Zone` và dữ liệu hiện hành. Không giả định `HQ_ENV=test` tự đổi DB.
4. Tạo branch tính năng; tạo revision mới trên head hiện tại với `archived_at` + `row_ver`; migration chỉ sửa schema, không thay metadata/đổi khu vực.
5. Chạy migration trên `HQTCSDL_test_nhan` (đối chiếu đúng máy/login thực tế). Kiểm tất cả hàng cũ còn nguyên PK/FK, `archived_at=NULL`, `row_ver` được sinh.
6. Viết/run test upgrade + schema/race cần thiết; Ruff, pytest, typecheck, build nếu phần thay đổi liên quan; ghi lệnh và output vào PR/Chương 04.
7. Review + merge PR A; **chỉ sau đó** mở PR B.

Lệnh Git mở đầu cho người triển khai trên PowerShell (không tự chạy database migration trước khi kiểm cấu hình):

```powershell
cd D:\uth\he_quan_tri_CSDL\HQTCSDL
git status
git fetch origin
git switch develop
git pull --ff-only origin develop
git log -1 --oneline
git switch -c feat/farm-zone-lifecycle-schema
cd backend
uv run alembic heads
uv run alembic current
```

Không dùng `stamp head`, `IDENTITY_INSERT`, xóa DB hoặc sửa migration đã áp để vượt lỗi SQL.

## 8. Ma trận kiểm thử tối thiểu (chưa nghiệm thu)

| Nhóm | Ca bắt buộc |
| --- | --- |
| Migration | Upgrade DB test hiện có; 4 cột mới, seed/data không mất, indexes/relationships giữ, rollback lỗi |
| F03-AC01–15 | Nhiều Farm/Zone, 0/1/nhiều Farm, exact BIN2, metadata, archived/restore, 409 version, 409 AREA_IN_USE |
| Context compatibility | 0 Farm→INVALID, 1 Farm→đọc được, 2 Farm→409 khi thiếu farm_id, 0 Zone active→VALID với `[]`, Farm archived, ID không tồn tại |
| Transaction SQL hai kết nối | Archive vs CREATE/RESTORE/ENABLE, concurrent PATCH stale kể cả no-op, không phantom, không deadlock do đảo thứ tự |
| FL-03 regression | Gateway farm_id; Node zone_id; immutable FK/code; create atomic runtime/state; exact lookup/cursor; logout/write race |
| Credential F13-KAC01–07 | Issue→201 và hash 32 byte; GET không leak; quota 2; hai issue đồng thời; revoke lặp; A/B rotation; lỗi phiên/CSRF/SQL |
| FL-06 | 1–50 records, five metrics, STORED/ALREADY_EXISTS/RETRY_LATER/REJECTED, source trước duplicate, archived area, revoke giữa batch, rollback |
| Simulator | Bản mới/trùng/conflict, mất HTTP response, offline queue, batch retry, ACK2 per record, không gọi đây là bằng chứng Flash/SD vật lý |
| FL-07 | Current theo Event Time; UNKNOWN; zero/null/status; snapshot pagination; shared view_token bảng/chart; đổi phạm vi |
| FE/security | AuthProvider logout/401/hết hạn, stale generation, CSRF/Origin, secret chỉ hiển thị một lần trong memory, không lộ qua log/storage |
| E2E | Login→chọn khu vực→hồ sơ→Gateway key→simulator→ingestion→SQL→history/chart; restart và dữ liệu bền |

**Nợ test từ PR #14 không bị xóa bởi thiết kế mới.** Chương 04 cần ghi rõ môi trường, command, commit, expected/actual, pass/fail/skipped và evidence; test mock không chứng minh các ràng buộc SQL/concurrency/locking.

## 9. Đồng bộ tài liệu và Definition of Done

Khi mỗi PR hoàn tất, cập nhật đúng trạng thái vào:

- Confluence 02.1/02.2 cho thay đổi yêu cầu; FL-02/FL-03/FL-06/FL-07 cho luồng; 03.3/03.4/03.5.1 cho kiến trúc/DB/API; 03.6 cho phụ thuộc và nhánh; **Chương 04** cho code/test evidence.
- Repository `README.md`, `docs/development.md`, `backend/.env.example` theo phạm vi chuyển đổi; không tài liệu hóa `HQ_AREA_MANIFEST` như cấu hình hiện hành sau khi tất cả caller đã migrate.
- Mỗi PR: link design/AC, thay đổi API/schema/DTO, migration dependency, output tests (kể cả skipped), race/fault chưa chạy, ảnh hưởng FE/IoT và rollback note.

**Definition of Done một PR:** code đúng contract, test tương ứng có bằng chứng, DB test dùng schema đúng, không lộ secret, tài liệu code đúng trạng thái, review và merge theo workflow. **Merge PR không tự động đồng nghĩa toàn bộ FL hoặc NFR đã nghiệm thu.**

## 10. Nguồn tham chiếu chính

| Nguồn | Link |
| --- | --- |
| GitHub develop/PR #14 | https://github.com/nhanhehehe/HQTCSDL/pull/14 |
| 01 — Tổng quan & phạm vi | https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/6553619 |
| 02.1 — F01/F03 Farm Zone | https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/6553663 |
| 02.2 — Node, Gateway, xác thực | https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/6586602 |
| FL-02 — Farm/Zone lifecycle | https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14680393 |
| FL-03 — Hồ sơ và credential | https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14713081 |
| FL-06 — Ingestion | https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14647572 |
| 03.1 — Contract Gateway–Backend | https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/13729794 |
| 03.4 — CSDL và migration delta | https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14352390 |
| 03.5.1 — Quy ước SQL/API | https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14680313 |
| 03.6 — Kế hoạch R1–R6 | https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/14647366 |
| 04 — Triển khai và kiểm thử | https://bengame2006.atlassian.net/wiki/spaces/HQTCSDL/pages/15368195 |

---

**Lưu ý phiên bản:** Tài liệu này là snapshot kế hoạch ngày 18/11/2026 dựa trên Confluence mới và `develop` ở `a846ac1`. Nếu có commit/migration mới sau mốc này, phải kiểm tra lại `HEAD`, trạng thái DB, changelog và AC trước khi thực hiện, không dùng số migration hoặc trạng thái test của snapshot như hiện trạng mặc định.
