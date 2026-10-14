# Quyền dữ liệu của AI Chatbox

Cấu hình này **chỉ áp dụng cho AI Chatbox**, không thay thế hệ thống phân quyền HR. Các truy vấn nhân sự được thực thi bởi backend đã xác thực, AI không nhận quyền truy cập SQL tự do.

## Nguyên tắc
- SELF: người dùng đọc dữ liệu của chính mình, soạn đơn và tạo báo cáo cá nhân theo employee_id từ session. Luôn được phép với role đã xác thực. Báo cáo HEADCOUNT, APPROVAL_QUEUE và PAYROLL_SUMMARY vẫn chặn EMPLOYEE.
- DIRECT_REPORTS: chỉ nhân viên thuộc cấp quản lý trực tiếp được xác định bởi ReportService/employee_scope.
- COMPANY: chỉ role HR hoặc ADMIN mới có mức quyền tối đa này; role MANAGER hoặc EMPLOYEE không thể được nâng quyền ở menu AI.
- Admin được bật/tắt phạm vi trong **Quyền truy cập AI**. Role ADMIN luôn giữ cấu hình gốc. Các giới hạn bổ sung được áp dụng trước truy vấn/snapshot cho các chức năng AI.
- Các tài liệu policy vẫn phải trải qua kiểm tra PUBLISHED, hiệu lực, minimum_role; chính sách theo phạm vi dữ liệu không thay thế quyền xem tài liệu.

## Triển khai
Chạy Alembic migration `c4d5e6f70819` **trước khi triển khai backend mới**. Nếu thiếu bảng, API /ai/chat và /ai/suggestions sẽ không tiếp tục xử lý, thay vì bỏ qua phân quyền.

Endpoints:
- `GET /api/v1/ai/access/roles`: chỉ ADMIN.
- `PUT /api/v1/ai/access/roles/{role_code}`: chỉ ADMIN; JSON `{"scopes":["SELF","DIRECT_REPORTS"]}`.

Cấu hình chỉ hạn chế quyền so với HR role, không cấp thêm quyền của HR. Những thay đổi được kiểm tra ở backend cho mỗi request, không tin dữ liệu role/scope do client đưa vào.

## Trạng thái kiểm thử
Đã bổ sung unit tests cho policy limits, quyền ghi Admin, report draft READY và minimization của date provider. Cần kiểm chứng riêng với PostgreSQL, migration Alembic, frontend build, các trường hợp nhiều role, và dữ liệu thực trước khi merge/deploy. Không bảo đảm nhận diện được mọi câu tiếng Việt; yêu cầu mơ hồ sẽ được hỏi lại mà không tự tạo dữ liệu.
