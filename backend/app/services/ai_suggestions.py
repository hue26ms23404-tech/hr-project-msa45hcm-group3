"""Server-owned suggestion registry; IDs never grant a capability."""

from dataclasses import dataclass
from fastapi import HTTPException
from app.core.authorization import capabilities


@dataclass(frozen=True)
class SuggestionDefinition:
    prompt: str
    capability: str
    reference_id: str
    needs_employee: bool = False


CATALOG_VERSION = "v1"
SUGGESTIONS = {
    "leave_balance": SuggestionDefinition("Tôi còn bao nhiêu ngày phép năm?", "leave.read_self", "leave_policy", True),
    "draft_leave": SuggestionDefinition("Soạn đơn xin nghỉ phép", "leave.draft_self", "leave_request", True),
    "attendance_fix": SuggestionDefinition("Tạo giải trình chấm công", "attendance.draft_fix_self", "attendance_policy", True),
    "my_attendance_report": SuggestionDefinition("Tạo báo cáo công của tôi tháng này", "report.read_self", "report_export", True),
    "my_payslip": SuggestionDefinition("Tạo báo cáo phiếu lương của tôi tháng trước", "report.read_self", "report_export", True),
    "leave_approval_queue": SuggestionDefinition("Mở các đơn nghỉ phép đang chờ tôi duyệt", "report.read_direct_reports", "leave_request", True),
    "attendance_approval_queue": SuggestionDefinition("Mở các giải trình chấm công đang chờ tôi duyệt", "report.read_direct_reports", "attendance_policy", True),
    "team_attendance_report": SuggestionDefinition("Tạo báo cáo công nhân viên trực tiếp tháng này", "report.read_direct_reports", "report_security", True),
    "team_leave_report": SuggestionDefinition("Tạo báo cáo nghỉ phép nhóm tháng này", "report.read_direct_reports", "report_security", True),
    "headcount_report": SuggestionDefinition("Mở thống kê headcount hiện tại", "report.headcount", "report_security"),
    "company_attendance_report": SuggestionDefinition("Tạo báo cáo công toàn công ty tháng này", "report.read_company", "report_security"),
    "payroll_summary": SuggestionDefinition("Tạo báo cáo tổng hợp lương tháng trước", "report.read_company", "report_security"),
    "knowledge_admin": SuggestionDefinition("Mở quản lý tài liệu nội quy", "knowledge.manage", "report_security"),
    "leave_policy": SuggestionDefinition("Quy định nghỉ phép hằng năm như thế nào?", "knowledge.read", "leave_policy"),
    "attendance_policy": SuggestionDefinition("Tôi cần làm gì để giải trình lượt chấm công?", "knowledge.read", "attendance_policy"),
    "report_security": SuggestionDefinition("Ai được xem báo cáo nhân sự?", "knowledge.read", "report_security"),
}


def suggestion_allowed(suggestion_id: str, roles: set[str], employee_id: int | None) -> bool:
    """Keep suggestion visibility consistent with the report catalog scopes."""
    definition = SUGGESTIONS.get(suggestion_id)
    if definition is None:
        return False
    actor_caps = capabilities(roles)
    if definition.capability not in actor_caps or (definition.needs_employee and employee_id is None):
        return False
    # HEADCOUNT can use SELF when an employee is linked, or COMPANY when
    # the authenticated role permits it. A manager without an employee
    # identity has neither scope and must not see a dead-end suggestion.
    if suggestion_id == "headcount_report":
        return employee_id is not None or "report.read_company" in actor_caps
    return True


def resolve_suggestion(suggestion_id: str, roles: set[str], employee_id: int | None):
    definition = SUGGESTIONS.get(suggestion_id)
    if definition is None:
        raise HTTPException(422, "AI_SUGGESTION_INVALID")
    if not suggestion_allowed(suggestion_id, roles, employee_id):
        raise HTTPException(403, "PERMISSION_DENIED")
    return definition


def section_unavailability(rows: list[dict], allowed_roles: list[str], today) -> str:
    """Internal-only reason; rows carry flags, never document content."""
    if not rows:
        return "DOCUMENT_NOT_FOUND"
    for row in rows:
        if row["document_status"] != "PUBLISHED" or row["version_status"] != "PUBLISHED":
            continue
        if row["document_role"] not in allowed_roles or row["version_role"] not in allowed_roles:
            continue
        if row["effective_from"] and row["effective_from"] > today or row["effective_to"] and row["effective_to"] < today:
            return "NOT_EFFECTIVE"
        if not row["is_answerable"]:
            return "SECTION_NOT_ANSWERABLE"
    if any(row["document_status"] == "PUBLISHED" and row["version_status"] == "PUBLISHED" for row in rows):
        return "INSUFFICIENT_PERMISSION"
    return "NOT_PUBLISHED"


def unique_visible_sections(rows: list[dict]) -> tuple[dict[str, dict], set[str]]:
    """Avoid claiming an arbitrary document as a canonical policy reference."""
    selected: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for row in rows:
        code = row["section_code"]
        if code in selected:
            ambiguous.add(code)
        else:
            selected[code] = row
    for code in ambiguous:
        selected.pop(code, None)
    return selected, ambiguous
