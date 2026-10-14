# Context dự án — tra cứu theo tác vụ

Nguồn thiết kế: [README](../README.md). Repo có frontend React/Vite và backend FastAPI/SQLAlchemy, migration, pytest và Docker Compose; code là bằng chứng implementation, chưa phải bằng chứng chạy thành công. Môi trường/lệnh và blocker xem [AGENTS](../AGENTS.md). Đọc riêng mục liên quan; DBML ở README §4.3, API ở §5. Các điểm chưa chốt tập trung ở §7, không phải yêu cầu đã được quyết định.

## 1. Nhân sự và phân quyền
- Hồ sơ nhân viên, phòng ban, chức vụ, quản lý trực tiếp; trạng thái `ACTIVE`, `INACTIVE`, `TERMINATED`. Một nhân viên gắn một tài khoản; tài khoản có các gán vai trò ADMIN/HR/MANAGER/EMPLOYEE.
- HR/ADMIN quản lý nhân sự và danh mục. Quản lý trực tiếp (`manager_employee_id`) là người duy nhất duyệt phép/giải trình của cấp dưới, không tự duyệt, không qua HR.
- Danh tính từ JWT; truy cập phải xét quyền và phạm vi dữ liệu, không chỉ role hay ID gửi lên.
- Bảng: `hr_departments`, `hr_positions`, `hr_employees`, `hr_user_accounts`, `hr_roles`, `hr_user_role_assignments`.
- Nguồn: README §1.5, §2 “Nguyên tắc kỹ thuật và bảo mật”, §3.1, §5.

## 2. Ngày lễ và lịch làm việc
- Thứ Hai–Thứ Sáu 08:00–17:00; nghỉ trưa 12:00–13:00; 480 phút = 1 công. Không chia ca, không làm cuối tuần.
- ADMIN/HR cấu hình `hr_holidays`. Lễ hưởng nguyên công không cần quét; ngày công chuẩn tháng vẫn bao gồm lễ trong các ngày làm việc.
- Cấm sửa/xóa lễ khi `holiday_date < CURRENT_DATE`. Ngày nghiệp vụ phải hiểu theo `Asia/Ho_Chi_Minh`.
- Nguồn: README §1.1, §1.4, §1.6, §3.2.

## 3. QR, công và giải trình
- HR phát hành/thu hồi/cấp lại thẻ tĩnh. Token opaque ngẫu nhiên, DB lưu SHA-256 hash. Kiosk web online gửi lượt `CHECK_IN`/`CHECK_OUT`, xác thực thiết bị, chống lặp bằng `idempotency_key`.
- Luồng: thẻ → sự kiện quét → tổng hợp ngày (vào đầu tiên, ra cuối cùng, phút làm việc, trạng thái) → dữ liệu đầu vào lương.
- Thiếu lượt: nhân viên gửi ngày, loại lượt, giờ đề xuất và lý do → quản lý trực tiếp duyệt/từ chối → nếu duyệt hợp lệ, tạo event `APPROVED_FIX` liên kết đơn → tổng hợp lại ngày.
- Event gốc append-only, không sửa/xóa. Thiếu lượt chưa được bổ sung được duyệt trước hạn chốt thì mất công; cần giải quyết mốc chốt ở §7.
- Bảng: `hr_qr_cards`, `hr_attendance_events`, `hr_attendance_days`, `hr_attendance_fixes`. Đơn giải trình: `PENDING`, `APPROVED`, `REJECTED`.
- Nguồn: README §1.2, §2, §3.3, §5 “Các quy tắc xử lý API trọng yếu”.

## 4. Nghỉ phép
- Một đơn theo một ngày và buổi: `MORNING` 08:00–12:00 hoặc `AFTERNOON` 13:00–17:00 = 0.5 ngày; `FULL_DAY` = 1 ngày.
- Loại phép trong danh mục/DBML: `ANNUAL`, `UNPAID`, `SICK`; tên nghỉ không lương chưa thống nhất toàn README.
- Luồng: nhân viên gửi đơn → kiểm tra số dư nếu ANNUAL → quản lý trực tiếp quyết định → dữ liệu nghỉ được duyệt tham gia tính công/lương.
- Nếu `remaining_days < requested_days`, chặn ANNUAL và yêu cầu chọn UNPAID; không tự chuyển loại. Số dư phép theo nhân viên/năm, hết hạn 31/12, không chuyển năm sau.
- Bảng: `hr_leave_types`, `hr_employee_leave_balances`, `hr_leave_requests`. Trạng thái đơn: `PENDING`, `APPROVED`, `REJECTED`, `CANCELLED`; chưa mô tả đầy đủ chuyển trạng thái.
- Nguồn: README §1.3, §3.4, §5 quy tắc 2.

## 5. Lương
- Kỳ tháng từ ngày 01 đến cuối tháng. HR quản lý lịch sử lương GROSS và phụ cấp theo hiệu lực.
- Luồng: công + lễ + phép đã duyệt + hồ sơ lương → tính ngày công chuẩn/thực tế hưởng lương → GROSS, bảo hiểm, thuế và NET → HR rà soát/chốt → xuất Excel/PDF, nhân viên xem phiếu của mình.
- DBML liệt kê kỳ `DRAFT`, `CALCULATED`, `APPROVED`, `CLOSED`; không suy ra đầy đủ quyền hay chuyển trạng thái từ danh sách này. Chi tiết dòng lương là snapshot, có `calculation_details`.
- README nêu BHXH 8%, BHYT 1.5%, BHTN 1%; thuế sau bảo hiểm và giảm trừ gia cảnh, theo biểu lũy tiến. Đây là thông tin thiết kế, chưa xác minh pháp lý cho kỳ áp dụng; trước triển khai phép tính phải xác minh nguồn chính thức và thời gian hiệu lực.
- Tiền dùng Decimal/`DECIMAL(18, 2)`. Công chuẩn bỏ T7/CN, gồm lễ thuộc ngày làm việc; tránh tính trùng công từ các nguồn.
- D01 đã được user chốt: phát hành phiếu cá nhân và xuất báo cáo lương ở APPROVED hoặc CLOSED; HR/ADMIN có quyền rà soát bản tính trong màn hình quản trị. Reports lấy PayrollLine snapshot, không tính lại; tổng hợp theo phòng ban hiện tại. Payroll hiện dùng VND, line chưa lưu snapshot tiền tệ khác.
- Bảng: `hr_employee_compensation`, `hr_payroll_periods`, `hr_payroll_lines`.
- Nguồn: README §1.6, §3.5, §5; thiếu chính sách tính cụ thể xem §7.

## 6. AI nội bộ
- Câu tự do mới đi qua Gemini `app/ai/parsing/intent.py` trước handler: intent + target + reference + truy vấn RAG, rồi backend kiểm JWT/scope và resolve nhân viên. Không thay mục tiêu người khác bằng dữ liệu bản thân. Cờ `GEMINI_SEMANTIC_ENABLED` mặc định bật. Khi nội bộ thiếu bằng chứng, `GEMINI_PUBLIC_SEARCH_ENABLED` cho phép Search grounding với nguồn nhà nước; quota Search riêng, không có nguồn thì không bịa kết luận. Không thêm migration cho luồng này; chi tiết ở [AI_COPILOT](AI_COPILOT.md).
- Chat là giao diện chung, không có tab báo cáo/lịch sử tải: hỏi filter thiếu, preview bảng/tải Excel, chỉnh từ run owner để tạo snapshot mới. Python tổng hợp DB/nhận dạng phổ biến; Gemini chỉ diễn giải schema đóng. AIService điều phối, AIEmployeeTool/AIKnowledgeTool truy xuất, ReportPromptInterpreter là adapter thay được. Kiến trúc/điểm mở rộng: [ARCHITECTURE](ARCHITECTURE.md#kiến-trúc-ai-chatbox-và-công-cụ).
- Tra cá nhân cần câu gốc chỉ bản thân, không tin riêng nhãn SELF. Parser giữ câu hỏi có “lý do”; draft input được xử lý theo owner/revision. Đổi scope báo cáo bỏ employee filter cũ, đổi kỳ giữ người; kind không hỗ trợ employee bỏ filter cũ. Chat không hiện nhà cung cấp/hạn mức; chi tiết chỉ dành chẩn đoán.
- Tra số dư/công theo JWT có thể kèm policy citation đúng quyền; thiếu phép cảnh báo sớm, không tự đổi loại. Upload PDF một bước tạo version DRAFT/section theo trang; quyền mặc định EMPLOYEE, HR kiểm tra rồi công bố.
- Nháp báo cáo nhiều lượt theo quyền, 7 loại Excel/snapshot private, lifecycle GENERATING/READY/FAILED/EXPIRED. Error envelope có mã/thông báo/request ID server; follow-up bắt buộc revision. PDF có version/section và viewer highlight. Chi tiết và hạn chế runtime: [AI_COPILOT](AI_COPILOT.md).
- `POST /api/v1/ai/chat`: RAG hỏi đáp chính sách; tool tra công, lượt thiếu và số dư phép của người dùng; hỗ trợ tạo phép/giải trình bằng ngôn ngữ tự nhiên.
- Luồng: hội thoại → nhận intent/tham số → truy xuất chính sách hoặc gọi service nghiệp vụ → kết quả. Tool phải dùng danh tính xác thực, cùng kiểm tra quyền và validation như thao tác thông thường.
- Copilot truyền nháp vào form để người dùng kiểm tra/gửi qua API nghiệp vụ; LLM không ghi đơn. Gemini async có timeout theo từng bước; RAG lexical kết hợp truy vấn ngữ nghĩa trên tài liệu đã công bố, lọc quyền trước truy xuất. Có Google Search grounding cho kiến thức pháp luật công khai; chưa có vector RAG hoặc function calling để LLM tự thực thi nghiệp vụ.
- ADMIN/HR quản lý kiến thức; quyền đọc tối thiểu EMPLOYEE < MANAGER < HR < ADMIN. Báo cáo/phân tích chỉ MANAGER/HR/ADMIN, EMPLOYEE không có kể cả SELF; quản lý giữ SELF/DIRECT_REPORTS, COMPANY/tổng hợp lương chỉ HR/ADMIN. Ma trận prompt: [AI_PROMPT_PERMISSIONS](AI_PROMPT_PERMISSIONS.md). Migration/API: [AI_COPILOT](AI_COPILOT.md).
- Kho tài liệu xem/tải PDF lưu trữ cho HR/ADMIN, đổi tên và khôi phục thành version DRAFT mới từ file/mapping cũ; phải kiểm tra/công bố để AI dùng. Hỏi chính sách/quy trình ưu tiên RAG, nguồn kèm link đúng section/trang; hướng dẫn luồng ở SETUP_GUIDE.
- Nguồn: README §2, §3.6, §5 quy tắc 5.

## 7. Điểm chưa chốt — hỏi khi tác vụ phụ thuộc
- **AI/báo cáo:** đã bỏ ADMIN bypass duyệt; áp quản lý trực tiếp và cấm tự duyệt. Draft có owner/revision/TTL, report có snapshot private và bảy template. Chưa kiểm chứng DB migration/seed, concurrency budget hoặc browser acceptance; unit mocks không chứng minh tích hợp.
- **Tên phép:** §1.3 dùng `UNPAID_LEAVE`; §3.4/DBML/API dùng `UNPAID`. Chốt mã chung trước schema/API.
- **QR:** §1.2 và danh mục bảng gọi thẻ chứa token hash; §2 mô tả token opaque, DB lưu hash. Chốt payload thẻ và cách xác thực, không đánh đồng token với hash.
- **Hạn giải trình:** §1.2 nói trước ngày chốt công cuối tháng; §5 nói trước lúc kỳ lương `CLOSED`. Chốt mốc, timezone và hành vi đơn còn chờ.
- **AI ghi dữ liệu:** §3.6 nói nháp hoặc submit sau xác nhận; §5 nói gọi service rồi trả mã đơn để xác nhận. Chốt bước xác nhận và cách tránh gửi trùng.
- **Công:** chưa đủ quy tắc đi muộn/về sớm, quy đổi phút thành công, nửa ngày phép kết hợp quét thiếu, lễ cuối tuần/nghỉ bù và nguồn thời gian quét.
- **Phép:** chưa chốt SICK hưởng lương/trừ quota thế nào, cấp quota, đặt giữ/trừ/hoàn số dư, đơn trùng và phê duyệt đồng thời.
- **Lương:** thiếu tham số thuế/người phụ thuộc, cơ sở/trần bảo hiểm, làm tròn, lương đổi giữa tháng, vào/nghỉ việc giữa tháng và điều chỉnh kỳ đã đóng.
- **Schema/luồng:** DBML dùng `timestamp` trong khi yêu cầu lưu UTC; chưa có mô hình thiết bị kiosk, cấu hình thuế hay bảng nháp AI. API liệt kê cả auth/login/refresh nhưng câu mở đầu nói toàn bộ API dùng JWT ngoại trừ kiosk; cần chốt ngoại lệ auth khi triển khai.

## Duy trì tài liệu
- README giữ thiết kế chi tiết. Chỉ cập nhật tóm tắt khi quyết định nghiệp vụ, kiến trúc hoặc trạng thái tooling thay đổi; ghi nguồn quyết định, xóa điểm chưa chốt đã được giải quyết.
- Không nhân bản DBML/API, nhật ký phiên hay kết quả tạm thời. Lệnh phát triển chỉ đưa vào AGENTS khi có căn cứ và ghi rõ trạng thái xác minh.
