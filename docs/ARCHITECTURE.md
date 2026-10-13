# SOFTWARE ARCHITECTURE DOCUMENT (SAD)
## HỆ THỐNG QUẢN LÝ NHÂN SỰ & CHẤM CÔNG THÔNG MINH (GROUP 3 HRMS)

Tài liệu này mô tả kiến trúc tổng thể của hệ thống **HR Management System (Group 3)**, đóng vai trò định hướng cho việc phát triển, bảo trì và tích hợp hệ thống.

---

### 1. Giới thiệu tổng quan & Phạm vi hệ thống

Hệ thống được thiết kế theo mô hình **Client - Server hiện đại** phục vụ quản trị nhân sự nội bộ cho doanh nghiệp với các khối chức năng chính:
* **Quản trị Tổ chức & Nhân sự:** Quản lý cơ cấu phòng ban, chức danh, hồ sơ nhân viên và quan hệ quản lý phân cấp trực tiếp (`manager_employee_id`).
* **Chấm công Kiosk & Thẻ QR tĩnh:** Nhận diện lượt vào/ra (`CHECK_IN`/`CHECK_OUT`) qua thiết bị Kiosk Web tại cửa văn phòng, chống quét lặp và tổng hợp ngày công tự động.
* **Xử lý Giải trình công & Lưu vết đối chiếu:** Luồng nhân viên gửi giải trình bổ sung giờ quét, Quản lý trực tiếp phê duyệt và lưu vết lịch sử phục vụ đối chiếu khiếu nại.
* **Quản lý Nghỉ phép & Làm việc từ xa (Remote):** Đơn xin nghỉ theo buổi/ngày, hạn mức phép năm và cơ chế duyệt phân cấp.
* **Tính lương & Thuế TNCN:** Tự động tính toán lương Gross/Net, bảo hiểm bắt buộc, thuế TNCN theo luật Việt Nam, khóa kỳ lương và xuất báo cáo Excel.
* **Trợ lý AI nội bộ:** Hỗ trợ hỏi đáp chính sách và tra cứu dữ liệu cá nhân theo ngữ cảnh.

---

### 2. Các nguyên tắc kiến trúc cốt lõi (Architectural Drivers)

1. **Clean Layered Architecture:** Tách bạch ranh giới giữa Tầng Trình diễn (Presentation), Tầng Định tuyến API (API Routing), Tầng Dịch vụ Nghiệp vụ (Domain Services) và Tầng Lưu trữ Dữ liệu (Persistence).
2. **Stateless Backend:** Backend FastAPI hoạt động hoàn toàn không phụ thuộc trạng thái phiên (session state) trên bộ nhớ máy chủ. Mọi yêu cầu được xác thực thông qua JWT Token độc lập.
3. **Async Non-blocking I/O:** Khai thác tối đa mô hình xử lý bất đồng bộ (`asyncio` kết hợp driver `asyncpg`) giúp tối ưu hiệu năng I/O và phục vụ nhiều kết nối đồng thời với lượng tài nguyên nhỏ.
4. **Separation of Concerns & Type Safety:** 
   * Frontend sử dụng TypeScript và Ant Design component-based.
   * Backend chuẩn hóa dữ liệu vào/ra bằng Pydantic v2 DTOs và SQLAlchemy 2.x Declarative Models.
5. **Độ tin cậy & Chống nghẽn (Fault-Tolerance):** Các thao tác tích hợp bên ngoài (LLM AI) được bao bọc bởi cơ chế timeout và xử lý đa luồng worker, đảm bảo không bao giờ làm nghẽn Event Loop chính của ứng dụng.

---

### 3. Kiến trúc C4 Model

#### 3.1. C4 - Level 1: Ngữ cảnh hệ thống (System Context)

```mermaid
C4Context
    title Sơ đồ Ngữ cảnh Hệ thống - Group 3 HRMS

    Person(employee, "Nhân viên (Employee)", "Chấm công Kiosk, theo dõi bảng công, xin nghỉ phép/remote, xem phiếu lương")
    Person(manager, "Quản lý trực tiếp (Manager)", "Xét duyệt giải trình chấm công và đơn nghỉ phép/remote của cấp dưới")
    Person(admin_hr, "Admin & HR", "Quản trị nhân sự, cấp thẻ QR, tính bảng lương, khóa kỳ và xuất báo cáo Excel")

    System(hrms_system, "Hệ thống HRMS Group 3", "Nền tảng quản lý nhân sự, chấm công, bảng lương và AI nội bộ")

    System_Ext(kiosk_device, "Thiết bị Kiosk Văn phòng", "Màn hình/Tablet camera đặt tại cửa văn phòng quét thẻ QR")
    System_Ext(gemini_api, "Google Gemini AI API", "Mô hình ngôn ngữ phục vụ hội thoại hỏi đáp chính sách")

    Rel(employee, hrms_system, "Tương tác qua Web SPA", "HTTPS / JSON")
    Rel(manager, hrms_system, "Phê duyệt đơn từ", "HTTPS / JSON")
    Rel(admin_hr, hrms_system, "Quản trị toàn quyền", "HTTPS / JSON")
    Rel(kiosk_device, hrms_system, "Gửi lượt quét QR", "HTTPS / X-Kiosk-Secret")
    Rel(hrms_system, gemini_api, "Xử lý hội thoại AI", "REST API")
```

---

#### 3.2. C4 - Level 2: Kiến trúc Container (Container Architecture)

```mermaid
graph TD
    subgraph Client_Tier ["Tầng Client (Frontend)"]
        SPA["Single Page Application<br/>(React 19 + TypeScript + Vite + Ant Design)"]
        Kiosk["Màn hình Kiosk Web<br/>(Camera WebRTC + jsQR Scanner)"]
    end

    subgraph Server_Tier ["Tầng Server (FastAPI Backend)"]
        Gateway["API Routers & Middleware<br/>(/api/v1/*, JWT Auth, RBAC, Kiosk Validator)"]
        
        subgraph Domain_Services ["Domain Business Services"]
            AttendanceSvc["Attendance Service"]
            LeaveSvc["Leave Service"]
            PayrollSvc["Payroll Service"]
            AISvc["AI Assistant Service"]
        end
    end

    subgraph Data_Tier ["Tầng Dữ liệu & Lưu trữ"]
        Postgres[("PostgreSQL 16 Database<br/>(SQLAlchemy 2.x Async Engine)")]
        ExcelEngine["Excel Report Engine<br/>(openpyxl memory streaming)"]
    end

    subgraph External_Tier ["Dịch vụ Bên ngoài"]
        GeminiService["Google Gemini LLM API"]
    end

    SPA -->|REST API / JSON| Gateway
    Kiosk -->|POST /kiosk/scan| Gateway

    Gateway --> AttendanceSvc
    Gateway --> LeaveSvc
    Gateway --> PayrollSvc
    Gateway --> AISvc

    AttendanceSvc --> Postgres
    LeaveSvc --> Postgres
    PayrollSvc --> Postgres
    PayrollSvc --> ExcelEngine
    AISvc --> Postgres
    AISvc -.->|Async Thread Worker| GeminiService
```

---

### 4. Phân tầng kiến trúc (Layered Architecture)

Hệ thống phân tách mạch lạc thành 4 tầng chính:

```text
┌────────────────────────────────────────────────────────┐
│ 1. PRESENTATION LAYER (React 19 + TypeScript + Vite)   │
│    - Pages, Components, Layouts, Router & AuthContext │
└──────────────────────────┬─────────────────────────────┘
                           │ HTTP REST / JSON
┌──────────────────────────▼─────────────────────────────┐
│ 2. API ROUTING & SECURITY LAYER (FastAPI)              │
│    - Endpoints, JWT Verification, RBAC Dependencies    │
│    - Request/Response Validation (Pydantic Schemas)    │
└──────────────────────────┬─────────────────────────────┘
                           │ Service Calls
┌──────────────────────────▼─────────────────────────────┐
│ 3. DOMAIN BUSINESS SERVICES LAYER                      │
│    - AttendanceService: Chấm công, Kiosk, Giải trình   │
│    - LeaveService: Nghỉ phép, Remote, Hạn mức quota    │
│    - PayrollService: Công chuẩn, Bảo hiểm, Thuế TNCN   │
│    - AIService: RAG chính sách, tra cứu ngữ cảnh       │
└──────────────────────────┬─────────────────────────────┘
                           │ Async ORM (SQLAlchemy 2.x)
┌──────────────────────────▼─────────────────────────────┐
│ 4. DATA PERSISTENCE LAYER                              │
│    - PostgreSQL 16 (Relational DB, Indexes, Relations) │
│    - Alembic (Database Migrations Version Control)     │
└────────────────────────────────────────────────────────┘
```

#### 4.1. Tầng Trình diễn (Presentation Layer - Frontend)
* **Kiến trúc SPA:** Xây dựng với React 19 và Vite, đóng gói ứng dụng tải nhanh, tương tác mượt mà.
* **Hệ thống Design System:** Ant Design v5 cung cấp các thành phần giao diện chuẩn nghiệp vụ (Table, Tabs, Modal, Form, Tooltip, Tag, Badge).
* **Quản lý Trạng thái & Định tuyến:**
  * `AuthContext` duy trì phiên làm việc, lưu trữ token và thông tin vai trò (`roles`).
  * `ProtectedRoute` bảo vệ các tuyến đường theo phân quyền RBAC (`ADMIN`, `HR`, `MANAGER`, `EMPLOYEE`).
  * Axios Client tập trung (`api/client.ts`) với Interceptor tự động gán Bearer Token và xử lý lỗi phản hồi thống nhất.

#### 4.2. Tầng Định tuyến & Bảo mật API (API & Security Layer)
* Sử dụng FastAPI hỗ trợ chuẩn OpenAPI 3.0 (Swagger UI tự động).
* **Dependency Injection (`deps.py`):**
  * `get_current_user`: Trích xuất và giải mã JWT token từ Header `Authorization`.
  * `require_roles`: Kiểm soát quyền truy cập chi tiết tại từng endpoint.
  * `verify_kiosk_secret`: Xác thực thiết bị Kiosk qua Header bí mật riêng biệt `X-Kiosk-Secret`.
* **Data Transfer Objects (DTO):** Pydantic v2 schemas chịu trách nhiệm validate dữ liệu đầu vào và lọc bỏ các trường nhạy cảm ở đầu ra.

#### 4.3. Tầng Xử lý Nghiệp vụ Lõi (Domain Services Layer)
Toàn bộ quy tắc nghiệp vụ được cô lập trong các Service độc lập, không phụ thuộc vào giao thức HTTP:
* **AttendanceService:** Điều phối sự kiện quét thẻ, nhận diện Check-in/Check-out tự động, tổng hợp giờ làm việc trong ngày và xử lý đơn giải trình quên chấm công.
* **LeaveService:** Thẩm định đơn nghỉ phép/remote theo buổi hoặc ngày, quản lý hạn mức phép năm và kiểm soát thẩm quyền xét duyệt của Quản lý trực tiếp.
* **PayrollService:** Tổng hợp ngày công thực tế, tính toán lương Gross/Net, các khoản trích nộp bảo hiểm bắt buộc, thuế TNCN và kết xuất file Excel bảng lương.
* **AIService:** Phân tích ý định câu hỏi, nạp ngữ cảnh dữ liệu cá nhân của nhân viên và tích hợp mô hình ngôn ngữ lớn (LLM).
  * `app/ai/orchestrator.py` định tuyến chat; `AIService.process_chat()` là facade tương thích. `app/ai/tool_registry.py` ánh xạ intent đã kiểm tra sang tool/capability cố định, không gọi lại `process_chat()` để phân loại. `app/ai/parsing/dates.py` trả trạng thái ngày có kiểu và ưu tiên quy tắc; chỉ gửi cụm ngày đã rút gọn cho provider khi cần. `app/ai/providers/gemini.py` là nơi duy nhất tạo client `google-genai`, giới hạn thời gian/token và xác thực JSON bằng Pydantic. Nháp qua `AIDraftService`, dữ liệu qua các HR tool hiện hữu.
  * `app/core/authorization.py` đọc role assignment từ tài khoản HR đã xác thực và cấp capability từ role đã biết; role lạ không thêm quyền. Scope, chủ sở hữu báo cáo/nháp và quyền tài liệu được kiểm ở dịch vụ/SQL. Log provider chỉ ghi request ID, operation, model, thời gian và mã kết quả; không ghi prompt, key, lý do nghỉ hoặc response thô. Không có migration AI mới.

#### 4.4. Tầng Lưu trữ Dữ liệu (Data Persistence Layer)
* **Cơ sở dữ liệu:** PostgreSQL 16 lưu trữ dữ liệu có cấu trúc, quan hệ chặt chẽ và toàn vẹn dữ liệu ACID.
* **Async ORM:** SQLAlchemy 2.x khai thác cú pháp `Mapped` type-safe, thực thi truy vấn bất đồng bộ qua `asyncpg`.
* **Migration:** Alembic quản lý lịch sử tiến hóa lược đồ cơ sở dữ liệu có phiên bản.

---

### 5. Kiến trúc Bảo mật (Security Architecture)

1. **Bảo mật Mật khẩu:** Băm mật khẩu một chiều sử dụng thuật toán tiêu chuẩn `bcrypt` với muối ngẫu nhiên (salt rounds an toàn).
2. **Xác thực Token (JWT Authentication):**
   * Sử dụng cơ chế ký số bảo mật HMAC-SHA256 (`HS256`).
   * `access_token` có thời hạn ngắn (8 tiếng, tương ứng ca làm việc), mang thông tin định danh và phân quyền.
3. **Phân quyền vai trò (Role-Based Access Control - RBAC):**
   * Hệ thống hỗ trợ 4 vai trò: `ADMIN`, `HR`, `MANAGER`, `EMPLOYEE`.
   * Quyền hạn được kiểm tra ở cấp độ Endpoint trước khi logic nghiệp vụ được thực thi.
4. **Bảo mật thiết bị Kiosk:**
   * Màn hình Kiosk quét thẻ giao tiếp với Backend qua mã khóa bí mật `X-Kiosk-Secret`.
   * Mã thẻ QR lưu trong cơ sở dữ liệu dưới dạng hash SHA-256; token thô không được lưu trữ trực tiếp.
5. **Toàn vẹn dữ liệu (Auditability):**
   * Sự kiện quét thẻ (`hr_attendance_events`) hoạt động theo nguyên tắc Append-Only (chỉ thêm mới, không sửa/xóa).
   * Mọi thao tác phê duyệt giải trình và đơn nghỉ phép đều lưu vết rõ ràng: ID người duyệt (`reviewer_employee_id`), thời gian duyệt (`reviewed_at`) và ghi chú phản hồi (`review_note`).

---

### 6. Mô hình Dữ liệu mức cao (High-Level Data Architecture)

Lược đồ cơ sở dữ liệu được tổ chức thành các phân vùng nghiệp vụ mạch lạc:

```mermaid
erDiagram
    %% Tổ chức & Người dùng
    hr_departments ||--o{ hr_employees : "thuộc phòng ban"
    hr_positions ||--o{ hr_employees : "chức vụ"
    hr_employees ||--o{ hr_employees : "quản lý trực tiếp"
    hr_employees ||--|| hr_user_accounts : "tài khoản"
    hr_user_accounts ||--o{ hr_user_role_assignments : "gán vai trò"
    hr_roles ||--o{ hr_user_role_assignments : "định nghĩa quyền"

    %% Chấm công
    hr_employees ||--o{ hr_qr_cards : "thẻ QR"
    hr_employees ||--o{ hr_attendance_events : "lượt quẹt thẻ"
    hr_employees ||--o{ hr_attendance_days : "ngày công"
    hr_employees ||--o{ hr_attendance_fixes : "đơn giải trình"

    %% Nghỉ phép & Remote
    hr_leave_types ||--o{ hr_leave_requests : "loại đơn"
    hr_employees ||--o{ hr_employee_leave_balances : "hạn mức phép"
    hr_employees ||--o{ hr_leave_requests : "đơn xin nghỉ"

    %% Lương & Thuế
    hr_employees ||--o{ hr_employee_compensation : "mức lương HĐ"
    hr_payroll_periods ||--o{ hr_payroll_lines : "chi tiết bảng lương"
    hr_employees ||--o{ hr_payroll_lines : "phiếu lương"
```

---

### 7. Thuộc tính phi chức năng & Vận hành (NFRs)

| Tiêu chuẩn | Mục tiêu thiết kế | Giải pháp kiến trúc |
| :--- | :--- | :--- |
| **Hiệu năng (Performance)** | Phản hồi API < 100ms với tác vụ thông thường | Asyncpg connection pool, thiết lập chỉ mục Index trên các trường truy vấn thường xuyên (`work_date`, `employee_id`, `status`). |
| **Khả năng mở rộng (Scalability)** | Hỗ trợ mở rộng quy mô máy chủ | Backend được thiết kế hoàn toàn Stateless, cho phép chạy song song nhiều worker/container phía sau Load Balancer. |
| **Độ tin cậy (Reliability)** | Hoạt động ổn định, phục hồi nhanh | Database container hóa với Docker Volume bền vững, endpoint `/health` phục vụ giám sát liveness/readiness. |
| **Khả năng bảo trì (Maintainability)** | Cấu trúc module hóa cao | Áp dụng Clean Architecture, phân tách rõ ràng Models/Schemas/Services/Routers. |

---

### 8. Sơ đồ tuần tự tương tác tổng thể (Request Lifecycle)

```mermaid
sequenceDiagram
    autonumber
    actor User as Người dùng / Thiết bị
    participant UI as React Frontend / Kiosk Web
    participant Router as FastAPI Router
    participant Auth as Auth & RBAC Dependencies
    participant Service as Business Domain Service
    participant DB as PostgreSQL Database

    User->>UI: Thao tác giao diện (Gửi yêu cầu / Quẹt thẻ)
    UI->>Router: Gửi HTTP Request kèm Token / Secret
    Router->>Auth: Kiểm tra tính hợp lệ & Quyền hạn vai trò
    Auth-->>Router: Xác thực thành công
    Router->>Service: Gọi phương thức xử lý nghiệp vụ
    Service->>DB: Thực thi truy vấn dữ liệu (Async Session)
    DB-->>Service: Dữ liệu kết quả
    Service-->>Router: Trả về thực thể nghiệp vụ
    Router-->>UI: Phản hồi HTTP 200 OK (JSON Serialization qua Pydantic)
    UI-->>User: Cập nhật giao diện trực quan
```
