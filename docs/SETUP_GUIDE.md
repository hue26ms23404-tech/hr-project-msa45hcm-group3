# HƯỚNG DẪN CÀI ĐẶT & CHẠY ỨNG DỤNG (SETUP GUIDE)
## HỆ THỐNG QUẢN LÝ NHÂN SỰ & CHẤM CÔNG THÔNG MINH (GROUP 3 HRMS)

Tài liệu này hướng dẫn các bước thiết lập môi trường và khởi chạy ứng dụng **HR Management System (Group 3)** trên máy tính cá nhân hoặc máy chủ phát triển.

---

### 1. Yêu cầu môi trường (Prerequisites)

Đảm bảo máy tính đã cài đặt các công cụ sau trước khi tiến hành:
* **Hệ điều hành:** macOS, Linux (Ubuntu/Debian), hoặc Windows (khuyến nghị dùng WSL2).
* **Git:** `>= 2.30`
* **Docker & Docker Compose:** Docker Engine `>= 24.0`, Docker Compose `>= v2.20`
* **Python:** Phiên bản `>= 3.10` (khuyến nghị Python 3.11 hoặc 3.12)
* **Node.js & npm:** Node.js `>= 18.x` (LTS 20.x) và npm `>= 9.x`

---

### 2. Chuẩn bị mã nguồn & Cấu hình môi trường

#### Bước 1: Clone kho mã nguồn
```bash
git clone https://github.com/duy26ms23415-web/hr-project-msa45hcm-group3.git
cd hr-project-msa45hcm-group3
```

#### Bước 2: Thiết lập file biến môi trường (`.env`)
Sao chép cấu hình từ file mẫu `.env.example`:
```bash
cp .env.example .env
cp .env.example backend/.env
```

Chatbox dùng cấu hình Gemini sẵn có trong `backend/.env`:

```dotenv
GEMINI_ENABLED=true
GEMINI_API_KEY=your-api-key
GEMINI_MODEL_NAME=gemini-3.5-flash-lite
```

Không có migration AI mới. Nếu không có key hoặc `GEMINI_ENABLED=false`, chat vẫn trả lời bằng quy tắc và tài liệu nội bộ khi hiểu được câu hỏi. Mã model trong ví dụ là cấu hình override; hãy xác minh model với chính API key và revision định triển khai, không tự thay model khi probe lỗi. Chạy `python -B -m app.ai.providers.diagnose` tại `backend/` để xem cấu hình an toàn; thêm `--live` chỉ khi được phép gọi provider có thể tính phí. Các lệnh migration/seed ở dưới chỉ chạy trên DB phù hợp.

---

### 3. Khởi chạy Cơ sở dữ liệu (PostgreSQL)

Hệ thống sử dụng PostgreSQL 16 chạy qua Docker Compose:

```bash
# Khởi chạy database chạy ngầm
docker compose up -d
```

Kiểm tra trạng thái container đang hoạt động:
```bash
docker ps
# Container `hr_postgres_group3` ở trạng thái Up (healthy) trên cổng 5432
```

---

### 4. Cài đặt & Khởi động Backend (FastAPI)

Mở Terminal thứ nhất:

```bash
# 1. Di chuyển vào thư mục backend
cd backend

# 2. Khởi tạo và kích hoạt môi trường ảo Python
python3 -m venv .venv
source .venv/bin/activate       # Trên Windows: .venv\Scripts\activate

# 3. Cài đặt các thư viện phụ thuộc
pip install --upgrade pip
pip install -r requirements.txt

# 4. Cập nhật cấu trúc database (Alembic Migration)
alembic upgrade head

# 5. Nạp dữ liệu mẫu ban đầu (Phòng ban, Nhân sự, Thẻ QR, Số dư phép, Tài khoản demo)
python -m app.scripts.seed_data

# 6. Khởi động máy chủ backend
uvicorn app.main:app --reload --port 8000
```

* Máy chủ Backend API: `http://127.0.0.1:8000`
* Tài liệu Swagger UI: `http://127.0.0.1:8000/api/v1/docs`
* Kiểm tra trạng thái: `http://127.0.0.1:8000/health`

---

### 5. Cài đặt & Khởi động Frontend (React + Vite)

Mở Terminal thứ hai:

```bash
# 1. Di chuyển vào thư mục frontend
cd frontend

# 2. Cài đặt các gói phụ thuộc npm
npm install

# 3. Khởi động máy chủ giao diện người dùng
npm run dev
```

* Ứng dụng Web chạy tại: `http://localhost:5173`
* Mọi request đến `/api/*` sẽ được Vite tự động chuyển tiếp (proxy) an toàn về Backend `http://127.0.0.1:8000`.

---

### 6. Tài khoản dùng thử (Demo Accounts)

Hệ thống đã nạp sẵn 3 tài khoản mẫu ứng với các vai trò để kiểm tra tính năng:

| Vai trò | Email đăng nhập | Mật khẩu | Phạm vi quyền hạn |
| :--- | :--- | :--- | :--- |
| **Admin & HR** | `admin@hrgroup3.com` | `Password@123` | Quản trị nhân sự, cấp thẻ QR, tạo & tính bảng lương, khóa kỳ lương, xuất Excel. |
| **Quản lý (Manager)** | `manager@hrgroup3.com` | `Password@123` | Chấm công cá nhân, phê duyệt đơn giải trình công và đơn xin nghỉ phép/remote của cấp dưới. |
| **Nhân viên (Employee)** | `employee@hrgroup3.com` | `Password@123` | Quẹt thẻ Kiosk, xem bảng công, gửi giải trình, nộp đơn nghỉ phép/remote, xem phiếu lương. |

*Giao diện đăng nhập tại `http://localhost:5173/login` có sẵn các nút bấm đăng nhập nhanh 1-click cho 3 tài khoản trên.*

---

### 7. Các địa chỉ truy cập quan trọng (Verification URLs)

* **Trang làm việc chính:** `http://localhost:5173` (Đăng nhập và truy cập Dashboard, Chấm công, Nghỉ phép, Bảng lương, Nhân sự).
* **Màn hình Kiosk quét mã QR:** `http://localhost:5173/kiosk` (Đặt tại cửa văn phòng để chấm công vào/ra).
* **Tài liệu API tương tác (Swagger):** `http://127.0.0.1:8000/api/v1/docs`.

---

### 8. Lệnh dừng hệ thống

* **Dừng Backend & Frontend:** Nhấn `Ctrl + C` tại cửa sổ Terminal đang chạy `uvicorn` và `npm run dev`.
* **Dừng Database Docker:**
  ```bash
  docker compose down
  ```
  *(Dữ liệu cơ sở dữ liệu vẫn được lưu trữ an toàn trong Docker Volume `postgres_data`).*
