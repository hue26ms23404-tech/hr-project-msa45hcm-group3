# Enterprise HR AI Copilot

## Sử dụng

Luồng Gemini ngữ nghĩa dùng API key/model hiện có (`GEMINI_ENABLED=true`); `GEMINI_SEMANTIC_ENABLED` mặc định bật. Câu tự do mới được giải nghĩa **trước** luật chọn handler tại `app/ai/parsing/intent.py`. JSON `ChatPlan` xác định intent, target, employee_reference nguyên văn, multiple_actions và truy vấn RAG; không gửi lịch sử/hồ sơ DB. Backend tìm nhân viên và kiểm quyền từ JWT; không chuyển yêu cầu về người khác thành dữ liệu người hỏi. Người dùng không đủ quyền nhận cảnh báo cùng nguồn phân quyền đã công bố nếu có. Provider lỗi trả trạng thái UNAVAILABLE, không tự chọn handler cá nhân. Gợi ý, nháp/run nhiều lượt vẫn dùng contract owner/revision hiện hữu; nhiều tác vụ được hỏi lại.

Quota do provider áp theo project; ứng dụng không tự retry. Hỏi chính sách có thể có ba bước: giải nghĩa, trả lời từ nguồn nội bộ, rồi Google Search khi không đủ bằng chứng. Mỗi bước có timeout/token limit, log operation/model/mã kết quả không ghi prompt hay key. Nguồn công khai chỉ nhận đoạn có grounding và link cơ quan nhà nước, hiển thị riêng với PDF công ty; không suy đoán chính sách riêng. Search có quota riêng và có thể lỗi dù sinh JSON hoạt động. Test unit mock provider; phải thử Search thật riêng khi triển khai.



- Nháp follow-up bắt buộc có draft_id/draft_revision; có thể gửi inputs có kiểu mà không thêm message giả. Báo cáo tự do hỏi loại/kỳ/phạm vi theo quyền qua DRAFT_REPORT, sau đó tạo run thật. Gợi ý mẫu giữ mặc định minh bạch.
- Lỗi AI/knowledge/reports trả code/message/request_id với action null; ID server khớp audit. Tham khảo gợi ý trỏ đúng LEAVE.REQUEST/REPORT.CATALOG/REPORT.ACCESS. PDF demo có mục lục/bookmarks/số trang và bản Markdown trong `docs/knowledge-drafts`.

- Chatbot lấy số dư phép năm và bản ghi công trong tháng theo danh tính JWT, ngày nghiệp vụ `Asia/Ho_Chi_Minh`. Không có số dư được báo là chưa có dữ liệu.
- Ví dụ: `Tôi còn bao nhiêu ngày phép?`, `Các ngày thiếu công của tôi?`, `Xin nghỉ sáng mai`, `Xin nghỉ Thứ Sáu tuần này`, `Quên check-out hôm qua lúc 17h`.
- Quick Action Cards truyền nháp vào form nghỉ phép/giải trình. Người dùng kiểm tra và gửi bằng API nghiệp vụ hiện có. Không có thao tác ghi dữ liệu từ LLM. Không tự đổi phép năm sang không lương; không đoán ngày hoặc giờ chấm công khi thiếu thông tin.
- Nếu thiếu ngày/buổi/loại/lý do/giờ, backend giữ draft owner-bound trong tối đa 15 phút và chatbot hỏi từng trường còn thiếu. Request follow-up chỉ gửi opaque `draft_id`; server đọc owner từ JWT, khóa row và chỉ cập nhật key theo command allowlist. Có thể hủy bằng “hủy bản nháp”. Draft hết hạn trả mã ổn định `AI_DRAFT_EXPIRED`; draft của user khác trả 404 chung.
- Câu hỏi nội quy dùng tối đa ba truy vấn ngữ nghĩa do Gemini diễn đạt lại trên tài liệu PUBLISHED đã lọc quyền, kết hợp câu gốc. Câu trả lời nội bộ vẫn phải có evidence nguyên văn và số liệu được kiểm tra. Khi không có/không đủ bằng chứng, `GEMINI_PUBLIC_SEARCH_ENABLED=true` cho phép tra luật/chính sách lao động công khai bằng Google Search; không gắn PDF không liên quan vào câu trả lời thiếu căn cứ.
- Gemini phân loại câu tự do vào enum đóng và target trước handler, không dựa cổng từ khóa HR để quyết định có gọi mô hình. Backend vẫn quyết định identity, quyền và thao tác. Thông tin liên hệ được che trước giải nghĩa; câu hỏi có “lý do” được giữ đầy đủ, tên/mã người được hỏi được giữ để resolve ở backend. SELF cho tra cứu/báo cáo cá nhân cần câu gốc chỉ rõ bản thân; câu mơ hồ hoặc nêu tên người khác không tự chuyển sang dữ liệu người đăng nhập. Lỗi giải nghĩa trả status UNAVAILABLE, mã chẩn đoán AI_INTENT_* và action null; chat chỉ thông báo thử lại dễ hiểu, không nêu nhà cung cấp/hạn mức. Ngoài HR không chạy tool.
- Nguồn tham khảo hiển thị ngay dưới câu trả lời. Mở nguồn gọi lại API kiểm tra quyền và trạng thái tài liệu hiện tại.
- `GET /api/v1/ai/suggestions` trả gợi ý theo role xác thực; câu hỏi chính sách chỉ có khi PDF tương ứng đang được công bố, còn hiệu lực và user có quyền đọc. Reference gắn section/trang mở viewer PDF có xác thực.
- Báo cáo tạo bằng ReportRun ngay trong chat; thông tin thiếu được hỏi thêm. AI không gửi/duyệt đơn hoặc chạy SQL; bản nháp mở form nghiệp vụ để người dùng kiểm tra và gửi.

## Kiến thức và quyền

- ADMIN/HR quản lý tài liệu tại `/knowledge`: PDF versioned riêng tư, section/page đã kiểm tra, trạng thái `DRAFT`/`PUBLISHED`/`ARCHIVED` và quyền tối thiểu; legacy text được giữ tương thích.
- Mức đọc kiến thức: `EMPLOYEE < MANAGER < HR < ADMIN`; nhiều vai trò dùng mức cao nhất. HR không đọc, sửa hoặc công bố tài liệu mức ADMIN. Vai trò không biết không được truy xuất kiến thức.
- Chỉ `PUBLISHED` được dùng trong chat; bộ lọc quyền chạy tại SQL trước khi retrieval và trước khi gửi dữ liệu sang Gemini. Lịch sử hội thoại là dữ liệu không đáng tin, chỉ nhận role user/assistant. Client không cấp quyền hoặc chọn danh tính cho copilot.
- Có ba PDF demo trong `backend/storage/tmp/ai-knowledge-demo`; chúng là bản nháp minh họa, chưa được công ty thông qua. Script seed chỉ development/test, cần xác nhận tường minh và tạo DRAFT.
- API: `GET/POST /api/v1/ai/knowledge`, `GET/PUT /api/v1/ai/knowledge/{document_id}`, `POST /api/v1/ai/chat`.

## Báo cáo

- `/reports` tạo ATTENDANCE, LEAVE, ATTENDANCE_FIX, APPROVAL_QUEUE, HEADCOUNT, MY_PAYSLIP và PAYROLL_SUMMARY theo kỳ/phạm vi được backend cho phép; JSON/CSV/XLSX dùng cùng scope. Bảy file template v1 có sheet thông tin/dữ liệu/tổng hợp; tiền vượt 15 chữ số lưu text chính xác. Giới hạn 10.000 dòng, không truncate. GET legacy có XLSX trực tiếp; UI dùng ReportRun snapshot/TTL.
- ReportRun API tạo bản xem trước và workbook private có TTL một giờ. Chỉ chủ sở hữu xem/tải; lúc đọc và tải backend kiểm tra hạn dùng, phân quyền hiện tại và các nhân viên trong scope snapshot. Script cleanup đánh dấu hết hạn và xóa artifact private đã liên kết.
- Chỉnh run hoặc draft báo cáo: scope được nêu lại thay thế employee filter cũ, kể cả COMPANY → COMPANY; đổi sang kind không hỗ trợ employee cũng bỏ filter cũ. Đổi kỳ hoặc kind tương thích vẫn giữ nhân viên. Filter mới luôn được validation và kiểm scope; không nới quyền khi đổi loại/phạm vi.
- EMPLOYEE không được tạo/xem/xuất/phân tích báo cáo kể cả SELF; tra cứu và màn hình cá nhân giữ quyền riêng. MANAGER dùng SELF/DIRECT_REPORTS cho report được cấp; HR/ADMIN thêm COMPANY. Phiếu lương report chỉ SELF cho các role được quyền report, tổng hợp lương chỉ HR/ADMIN và không tính lại PayrollLine snapshot. Kỳ trọn tháng APPROVED/CLOSED, tổng tách mã tiền tệ; legacy chưa xác minh bị từ chối. Gợi ý/chat/API kiểm cùng quyền; [ma trận prompt](AI_PROMPT_PERMISSIONS.md). Audit phân tích cần migration 0a1b2c3d4e5f.
- Công chỉ đếm các trạng thái/bản ghi đã lưu, không suy diễn ngày thiếu bản ghi hoặc kết quả chốt lương. Ngày phép được duyệt gồm tất cả loại phép trong kỳ. Số dư phép năm là số dư hiện tại của năm được chọn, không phải số dư lịch sử tại cuối kỳ.
- API: `GET /api/v1/reports?kind=ATTENDANCE&start_date=2026-10-01&end_date=2026-10-31&output=json`; `output=csv` để tải file.

## Thiết lập và kiểm tra

- Dependencies PDF/generator/viewer có trong manifest/lock; xem SETUP_GUIDE. Gemini sử dụng provider và cấu hình đã có; không cần API key để chạy rule/RAG fallback.
- Có migration `a1b2c3d4e5f6` thêm `hr_ai_knowledge_documents`. Tại backend, áp dụng bằng `alembic upgrade head` trong môi trường và DB phù hợp theo SETUP_GUIDE. Không tự chạy migration hoặc seed trên DB chưa xác định.
- Unit/API tests không kết nối DB: trong backend dùng interpreter `hr-backend` chạy `python -B -m pytest --confcutdir=tests/unit tests/unit -q -p no:cacheprovider`. Mock Gemini và session; cần kiểm thử tích hợp trên DB test riêng để xác nhận migration và CRUD thực tế.
- Frontend kiểm tra bằng scripts `npm run build` và `npm run lint`; các trang kiến thức được tải bằng React lazy.
- Bằng chứng/gate chưa hoàn tất ở [AI_ACCEPTANCE](AI_ACCEPTANCE.md); head currency migration là d9e0f1a2b3c4. Edge viewer harness dùng API fixture, không chứng minh SQL authorization.

- Migration `c8d9e0f1a2b3` thêm audit/budget: 20 chat và 5 lần tạo report/tài khoản/phút, advisory lock PostgreSQL dùng chung process. Audit enum metadata, không raw prompt/reason/history. Script `python -B -m scripts.cleanup_ai_audit` dry-run retention 90 ngày; `--apply` mới xóa. SQLAlchemy ẩn bind parameters.
- Kiểm tra hiện tại: 63 unit/API tests dùng mock passed; frontend build passed, còn warning chunk >500 kB. Docker không có trong PATH phiên này; chưa chạy migration/seed hoặc acceptance trên DB/browser. Kiểm tra mocked không chứng minh scope SQL thực thi hoặc concurrency nhiều process.


## Mở rộng công cụ trong phạm vi AI

Các lệnh mẫu là capability của cùng chat, không cần tab AI riêng. Xem bảng module/luồng tại [ARCHITECTURE](ARCHITECTURE.md#kiến-trúc-ai-chatbox-và-công-cụ).

Thêm lệnh: khai báo suggestion/điều kiện trong registry, thêm intent đóng nếu cần provider hỗ trợ, dispatch tới service nghiệp vụ có kiểm quyền, mở rộng schema action/validator và UI, rồi kiểm thử trái quyền/fallback. Không cho provider trả SQL, employee IDs, route tùy ý hoặc template path. Giữ Adapter tách khỏi service nghiệp vụ; ReportPromptInterpreter là điểm thay interpreter. Python xử lý dữ liệu/thuật toán xác định, Gemini hỗ trợ nhận dạng ngôn ngữ và chọn tham số trong contract.

Số dư phép/công lấy ORM theo actor, có thể kèm policy citation đã lọc quyền. Không có tài liệu vẫn trả DB, không dựng chính sách. Thiếu số dư được báo sớm; người dùng chọn loại nghỉ và gửi ở form. Hỏi số dư trong lúc soạn giữ state/revision, không biến câu hỏi thành lý do.

Báo cáo preview/download trong chat, không tab báo cáo/lịch sử tải. Chỉnh dùng run owner làm context, tạo snapshot mới. Interpreter chỉ nhận prompt tối đa 600 ký tự + filter/template metadata, không DB rows/history/account. Kỳ phổ biến chạy Python; lỗi/timeout hỏi phần thiếu. Upload PDF một bước, section theo trang, quyền mặc định EMPLOYEE; version DRAFT phải được kiểm tra/công bố trước khi trích nguồn.

Hỏi chính sách ưu tiên trước soạn đơn. Gemini diễn giải động nguồn đã lọc quyền theo JSON statements (source/text/evidence); backend kiểm dẫn chứng và số liệu, route citation do server tạo. Provider lỗi dùng tối đa ba câu liên quan hoàn chỉnh; không dán cover/bảng quản trị PDF. Nội dung ghi dự thảo/chưa xác nhận luôn được nhắc rõ, không coi ví dụ là quy định có hiệu lực. Đây chưa phải kiểm chứng ngữ nghĩa hoàn chỉnh. Timeout policy riêng 15s; hướng dẫn/test ở SETUP_GUIDE. Kho có xem/tải archive cho HR/Admin, đổi nhãn và restore thành draft mới từ file/mapping cũ; công bố có màn review mở trực tiếp từ list.
