"""Dispatch a validated language plan through authenticated business services."""
from app.ai.parsing.intent import interpret_chat_request
from app.ai.providers.gemini import ProviderFailure, record
from app.schemas.ai import AIChatResponse
from app.services.ai_employee_query import EmployeeQuery, answer_employee_query, clarify, explicitly_requests_self, mentions_employee
from app.services.ai_service import AIService


async def process_semantic_chat(db, employee_id, req, roles, owner_id, actor, today):
    try:
        plan = await interpret_chat_request(req.message, today)
    except ProviderFailure as exc:
        record("interpret_chat_request", "FALLBACK_USED", category=exc.category)
        return AIChatResponse(
            reply="Hiện tôi chưa xử lý được yêu cầu này. Bạn vui lòng thử lại sau hoặc chọn một gợi ý có sẵn. Tôi chưa thực hiện thao tác nào.",
            status="UNAVAILABLE", message_code=f"AI_INTENT_{exc.category}",
        )
    if plan.multiple_actions:
        return clarify("MULTIPLE_ACTIONS_REQUIRED", "Yêu cầu có nhiều tác vụ hoặc nhiều nhân viên. Bạn muốn thực hiện việc nào trước? Vui lòng chọn một tác vụ và một đối tượng.")
    if plan.intent == "OUT_OF_SCOPE":
        return AIChatResponse(reply="Tôi hỗ trợ nghiệp vụ HR và chính sách lao động trong phạm vi được cấp quyền.", answer_mode="OUT_OF_SCOPE")
    if plan.intent == "POLICY_QUERY":
        return await AIService._answer_knowledge(db, roles, today, req, policy_question=True,
                                                 retrieval_queries=plan.retrieval_queries)
    if plan.target == "SELF" and mentions_employee(req.message):
        return clarify("TARGET_CONFLICT", "Yêu cầu có nhắc nhân viên khác. Vui lòng xác định rõ đối tượng; tôi chưa tra cứu dữ liệu bản thân.")
    reads_records = plan.intent in {"LEAVE_BALANCE", "ATTENDANCE_SUMMARY", "ANALYZE_REPORT"} or plan.intent.startswith("REPORT_")
    if (plan.target == "SELF" and reads_records
            and not explicitly_requests_self(req.message)):
        return clarify("TARGET_REQUIRED", "Bạn muốn tra cứu bản thân hay nhân viên nào? Vui lòng nêu rõ đối tượng; tôi chưa tra cứu dữ liệu.")
    if plan.target == "EMPLOYEE":
        if plan.intent not in {"LEAVE_BALANCE", "ATTENDANCE_SUMMARY", "REPORT_ATTENDANCE"}:
            return clarify("EMPLOYEE_ACTION_UNSUPPORTED", "Tôi hỗ trợ xem phép và báo cáo chấm công theo nhân viên trong phạm vi được cấp quyền. Tác vụ này chưa được hỗ trợ cho nhân viên khác.")
        return await answer_employee_query(db, actor, req, today, interpretation=EmployeeQuery(
            intent="LEAVE_BALANCE" if plan.intent == "LEAVE_BALANCE" else "REPORT_ATTENDANCE",
            employee_reference=plan.employee_reference,
        ))
    if plan.target == "UNSPECIFIED":
        return clarify("TARGET_REQUIRED", "Bạn muốn tra cứu bản thân, nhân viên nào hay phạm vi nhóm/công ty? Vui lòng nêu rõ đối tượng; tôi chưa tra cứu dữ liệu.")
    intent = plan.intent
    if plan.target in {"DIRECT_REPORTS", "COMPANY"}:
        intent = {"LEAVE_BALANCE": "REPORT_LEAVE", "ATTENDANCE_SUMMARY": "REPORT_ATTENDANCE"}.get(intent, intent)
    personal_intents = {"LEAVE_BALANCE", "ATTENDANCE_SUMMARY", "DRAFT_LEAVE", "DRAFT_FIX", "REPORT_PAYSLIP"}
    if intent in personal_intents and plan.target != "SELF":
        return clarify("TARGET_REQUIRED", "Tác vụ này cần xác định rõ đối tượng. Vui lòng chọn bản thân hoặc nêu nhân viên/phạm vi muốn xem.")
    scope = plan.target if plan.target in {"SELF", "DIRECT_REPORTS", "COMPANY"} else None
    result = await AIService._dispatch_free_intent(db, employee_id, owner_id, roles, req,
                                                  intent, actor, requested_scope=scope)
    return result if result is not None else clarify("CLARIFICATION_REQUIRED")
