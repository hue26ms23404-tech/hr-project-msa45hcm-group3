"""Dispatch a validated language plan through authenticated business services."""
from app.ai.parsing.intent import interpret_chat_request
from app.ai.providers.gemini import ProviderFailure, record
from app.schemas.ai import AIChatResponse
from app.services.ai_employee_query import EmployeeQuery, answer_employee_query, clarify, explicitly_requests_self, mentions_employee
from app.services.ai_service import AIService
from app.services.ai_report_draft_service import AIReportDraftService
from app.services.report_catalog import report_catalog
from app.ai.tool_registry import intent_for_tool


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
    if plan.report_kind and (plan.tool_id or not plan.intent.startswith("REPORT_")):
        return clarify("PLAN_CONFLICT",
                       "Yêu cầu chưa xác định thống nhất chức năng báo cáo cần dùng.")
    if plan.tool_id:
        # The model may select a registered capability, never a callable or SQL.
        # The actual tool still performs RBAC/resource checks before execution.
        resolved = intent_for_tool(plan.tool_id)
        if resolved is None:
            return clarify("TOOL_UNAVAILABLE", "Chức năng được yêu cầu chưa được hỗ trợ.")
        if plan.intent not in {resolved, "OUT_OF_SCOPE"} and not (
                plan.needs_policy and plan.intent == "POLICY_QUERY"):
            return clarify("PLAN_CONFLICT",
                           "Tôi chưa xác định thống nhất công cụ phù hợp với yêu cầu.")
        plan = plan.model_copy(update={"intent": resolved})
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
    # Composite answers can combine a read-only business lookup with grounded
    # policy evidence. Never auto-execute draft/report writes on policy claims:
    # the policy document is not an executable eligibility decision.
    if plan.needs_policy:
        if intent not in {"LEAVE_BALANCE", "ATTENDANCE_SUMMARY"}:
            knowledge = await AIService._answer_knowledge(
                db, roles, today, req, policy_question=True,
                retrieval_queries=plan.retrieval_queries)
            if knowledge.answer_mode == "OUT_OF_SCOPE":
                return knowledge
            return knowledge.model_copy(update={
                "reply": knowledge.reply + "\n\nTôi chưa thực hiện thao tác nghiệp vụ. "
                         "Nếu muốn tiếp tục, hãy gửi riêng yêu cầu tạo báo cáo hoặc soạn bản nháp.",
                "message_code": "POLICY_ACTION_DEFERRED",
            })
        business = await AIService._dispatch_free_intent(
            db, employee_id, owner_id, roles, req, intent, actor, requested_scope=scope)
        if business is None or business.status != "OK":
            return business if business is not None else clarify("CLARIFICATION_REQUIRED")
        knowledge = await AIService._answer_knowledge(
            db, roles, today, req.model_copy(update={
                "message": plan.policy_question or req.message,
            }), policy_question=True, retrieval_queries=plan.retrieval_queries)
        # Evidence indices [1], [2] in knowledge.reply refer to the first
        # knowledge.sources; keep that ordering when appending business links.
        sources = list(knowledge.sources)
        used = {(s.document_id, s.version_id, s.section_id) for s in sources}
        for source in business.sources:
            key = (source.document_id, source.version_id, source.section_id)
            if key not in used:
                sources.append(source)
                used.add(key)
        return business.model_copy(update={
            "reply": business.reply + "\n\nChính sách liên quan:\n" + knowledge.reply,
            "sources": sources, "answer_mode": "RAG" if knowledge.sources else business.answer_mode,
        })
    # Report kinds are discovered from the registered report catalog, not
    # guessed by keyword or accepted as an unvalidated model-side command.
    if intent.startswith("REPORT_") and plan.report_kind:
        if actor is None:
            return clarify("REPORT_TARGET_REQUIRED")
        available = {entry["kind"] for entry in report_catalog(actor)["reports"]
                     if scope in entry["scopes"]}
        if plan.report_kind not in available:
            return clarify("REPORT_KIND_OR_SCOPE_UNAVAILABLE",
                           "Loại báo cáo hoặc phạm vi bạn yêu cầu không khả dụng. "
                           "Vui lòng chọn báo cáo trong danh mục được cấp quyền.")
        return await AIReportDraftService.start(
            db, actor, req.message, today,
            {"kind": plan.report_kind, "scope": scope})
    result = await AIService._dispatch_free_intent(db, employee_id, owner_id, roles, req,
                                                  intent, actor, requested_scope=scope)
    return result if result is not None else clarify("CLARIFICATION_REQUIRED")
