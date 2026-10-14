from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import pytest
from sqlalchemy.dialects import postgresql
from app.schemas.ai import AIChatRequest
from app.services.ai_service import AIService, requested_date
from app.services.report_service import employee_scope, safe_csv
from app.services.leave_service import LeaveService
from app.services.attendance_service import AttendanceService


def test_retrieval_prefers_matching_heading_and_links_each_section_once():
    from app.services.knowledge_retrieval import retrieve
    content = "Quy trình xin nghỉ phép năm được quy định trong tài liệu này. " * 40
    documents = [{"document_id": 1, "version_id": 2, "section_id": index, "title": "HR", "status": "PUBLISHED",
                  "heading": heading, "content": content} for index, heading in [(3, "Đối tượng áp dụng"), (4, "Quy trình xin nghỉ phép")]]
    passages = retrieve("Quy trình xin nghỉ phép", documents)
    assert passages[0].section_id == 4
    assert len(passages) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "Chính sách xin nghỉ phép như thế nào?",
    "Quy trình tạo đơn nghỉ phép là gì?",
    "Phân tích chính sách nghỉ phép",
    "Hướng dẫn tạo đơn nghỉ phép",
    "Cách xin nghỉ phép",
])
async def test_policy_questions_answer_from_pdf_and_link_section_without_creating_draft(monkeypatch, question):
    from app.services.ai_draft_service import AIDraftService
    monkeypatch.setattr("app.services.ai_service.settings.GEMINI_ENABLED", False)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [{
        "document_id": 3, "title": "Chính sách nghỉ phép", "status": "PUBLISHED",
        "content": "Chính sách nghỉ phép: quy trình xin nghỉ và tạo đơn nghỉ phép được hướng dẫn tại mục này.",
        "version_id": 5, "section_id": 8, "section_code": "LEAVE_REQUEST",
        "heading": "Quy trình xin nghỉ phép", "page_start": 2, "page_end": 3, "is_answerable": True,
    }]))
    draft = AsyncMock(side_effect=AssertionError("Policy questions must not create drafts"))
    personal = AsyncMock(side_effect=AssertionError("Policy questions must not read personal data"))
    monkeypatch.setattr(AIDraftService, "create_draft", draft)
    monkeypatch.setattr(AIService, "get_employee_context", personal)
    result = await AIService.process_chat(db, 7, AIChatRequest(message=question), {"EMPLOYEE"}, owner_user_account_id=9)
    assert result.action is None and result.draft_id is None
    assert result.answer_mode == "RAG"
    assert "quy trình xin nghỉ" in result.reply
    assert result.sources[0].viewer_path == "/knowledge/view/3?version=5&section=8"
    draft.assert_not_awaited()
    personal.assert_not_awaited()


@pytest.mark.asyncio
async def test_knowledge_management_suggestion_still_opens_editor_without_retrieval():
    db = AsyncMock()
    result = await AIService.process_chat(db, None, AIChatRequest(message="Mở tài liệu", suggestion_id="knowledge_admin"), {"HR"})
    assert result.action.action_type == "OPEN_KNOWLEDGE"
    db.execute.assert_not_awaited()


def user(role):
    return SimpleNamespace(employee_id=7, role_assignments=[SimpleNamespace(role=SimpleNamespace(role_code=role))])


def test_report_scope():
    from fastapi import HTTPException
    employee = str(employee_scope(user("EMPLOYEE")).compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "employee_id = 7" in employee and "manager_employee_id" not in employee
    manager = str(employee_scope(user("MANAGER")).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "manager_employee_id = 7" in manager and " OR " not in manager
    assert "employee_id = 7" in str(employee_scope(user("MANAGER"), "SELF").compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert employee_scope(user("HR"), "COMPANY") is None
    assert employee_scope(user("ADMIN"), "COMPANY") is None


def test_report_scope_fails_closed_for_unknown_roles():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        employee_scope(user("UNKNOWN"))
    assert exc.value.status_code == 403


def test_csv_does_not_execute_formulas():
    csv = safe_csv([{"name": "=HYPERLINK(bad)", "balance": "0.50"}], ["name", "balance"])
    assert "'=HYPERLINK" in csv
    assert "0.50" in csv


@pytest.mark.asyncio
async def test_leave_review_requires_another_current_direct_manager():
    from fastapi import HTTPException
    db = AsyncMock()
    leave_request = SimpleNamespace(
        leave_request_id=2, employee_id=20, employee=SimpleNamespace(manager_employee_id=20), status="PENDING"
    )
    review = SimpleNamespace(status="REJECTED", review_note=None)

    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: leave_request),
        SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(manager_employee_id=20)),
    ]
    with pytest.raises(HTTPException) as self_review:
        await LeaveService.review_leave_request(db, 2, 20, review)
    assert self_review.value.status_code == 403
    lock_query = str(db.execute.call_args_list[1].args[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in lock_query
    db.flush.assert_not_called()

    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: leave_request),
        SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(manager_employee_id=20)),
    ]
    with pytest.raises(HTTPException) as unrelated_admin:
        await LeaveService.review_leave_request(db, 2, 30, review)
    assert unrelated_admin.value.status_code == 403
    db.flush.assert_not_called()

    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: leave_request),
        SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(manager_employee_id=30)),
    ]
    await LeaveService.review_leave_request(db, 2, 30, review)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_attendance_review_rejects_self_and_allows_direct_manager_only():
    from fastapi import HTTPException
    db = AsyncMock()
    fix = SimpleNamespace(
        attendance_fix_id=3, employee_id=20, employee=SimpleNamespace(manager_employee_id=20),
        status="PENDING", work_date=date(2026, 10, 1), reviewer_employee_id=None,
    )
    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: fix),
        SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(manager_employee_id=20)),
    ]
    review = SimpleNamespace(status="REJECTED", review_note=None)

    with pytest.raises(HTTPException) as self_review:
        await AttendanceService.review_fix_request(db, 3, 20, review)
    assert self_review.value.status_code == 403
    lock_query = str(db.execute.call_args_list[1].args[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in lock_query
    db.flush.assert_not_called()

    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: fix),
        SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(manager_employee_id=30)),
        SimpleNamespace(scalar_one_or_none=lambda: None),
    ]
    await AttendanceService.review_fix_request(db, 3, 30, review)
    db.flush.assert_awaited_once()


def test_dates_are_explicit_and_valid():
    today = date(2026, 10, 5)
    assert requested_date("xin nghỉ sáng mai", today) == "2026-10-06"
    assert requested_date("nghỉ 12/10/2026", today) == "2026-10-12"
    assert requested_date("nghỉ 2026-02-30", today) is None
    assert requested_date("xin nghỉ", today) is None
    assert requested_date("Thứ Sáu tuần này", today) == "2026-10-09"
    assert requested_date("Thứ Hai tuần sau", today) == "2026-10-12"


@pytest.mark.asyncio
async def test_leave_balance_query_uses_requested_leave_year(monkeypatch):
    import app.services.ai_service as module
    monkeypatch.setattr(module, "business_today", lambda: date(2026, 12, 31))
    db = AsyncMock()
    db.execute.side_effect = [SimpleNamespace(scalar_one_or_none=lambda: None), SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])), SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))]
    result = await AIService.get_employee_context(db, 7, leave_year=2027)
    sql = str(db.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "year = 2027" in sql and result["remaining_leave_days"] is None


def test_draft_parser_keeps_only_explicit_allowlisted_fields():
    leave = AIService._draft_answers("DRAFT_LEAVE", "Xin nghỉ sáng ngày 2026-10-12 vì đi khám")
    assert leave == {"leave_date": "2026-10-12", "session": "MORNING", "reason": "đi khám"}
    fix = AIService._draft_answers("DRAFT_FIX", "Quên check-out ngày 2026-10-12 lúc 17h vì quên quẹt thẻ")
    assert fix == {"work_date": "2026-10-12", "event_type": "CHECK_OUT", "requested_time": "17:00:00", "reason": "quên quẹt thẻ"}
    assert "reason" not in AIService._draft_answers("DRAFT_LEAVE", "Tôi muốn xin nghỉ phép")


def test_medical_reason_does_not_select_sick_leave():
    assert AIService._requested_leave_code("Xin nghỉ sáng mai vì khám bệnh") is None
    assert AIService._requested_leave_code("Xin nghỉ phép năm vì bị ốm") == "ANNUAL"
    assert AIService._requested_leave_code("Xin nghỉ ốm sáng mai") == "SICK"


@pytest.mark.asyncio
async def test_forged_privileged_suggestion_is_denied_before_database():
    from fastapi import HTTPException
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await AIService.process_chat(db, 7, AIChatRequest(message="run", suggestion_id="payroll_summary"), {"EMPLOYEE"})
    assert exc.value.status_code == 403
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_suggestion_inputs_cannot_widen_report_scope_or_enter_policy_handler():
    from fastapi import HTTPException
    account = SimpleNamespace(user_account_id=9, employee_id=7, role_assignments=user("EMPLOYEE").role_assignments)
    db = AsyncMock()
    for suggestion, inputs, status in [("my_attendance_report", {"scope": "COMPANY"}, 403), ("leave_policy", {"reason": "extra"}, 422)]:
        with pytest.raises(HTTPException) as caught:
            await AIService.process_chat(db, 7, AIChatRequest(suggestion_id=suggestion, inputs=inputs), {"EMPLOYEE"}, owner_user_account_id=9, authenticated_user=account)
        assert caught.value.status_code == status
    db.execute.assert_not_called()
    db.flush.assert_not_called()


def test_action_rejects_unknown_fields_and_arbitrary_routes():
    from pydantic import ValidationError
    from app.schemas.ai import AIChatAction
    for payload in (
        {"action_type": "NAVIGATE", "data": {"path": "https://untrusted.test"}},
        {"action_type": "DRAFT_LEAVE", "data": {"leave_date": "2026-10-12", "session": "MORNING", "leave_type_id": 1, "employee_id": 99}},
        {"action_type": "OPEN_APPROVALS", "data": {"path": "/attendance", "target": "leave", "tab": "approvals"}},
    ):
        with pytest.raises(ValidationError):
            AIChatAction.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("suggestion_id", ["leave_policy", "attendance_policy", "report_security"])
async def test_policy_suggestion_without_sources_never_calls_provider(monkeypatch, suggestion_id):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
    classify = AsyncMock(side_effect=AssertionError("Policy fallback must not call Gemini"))
    monkeypatch.setattr(AIService, "_classify_free_intent", classify)
    result = await AIService.process_chat(db, None, AIChatRequest(message="run", suggestion_id=suggestion_id), {"HR"})
    assert result.message_code == "KNOWLEDGE_NOT_FOUND" and result.action is None
    classify.assert_not_called()


@pytest.mark.asyncio
async def test_draft_followup_uses_owner_bound_state_and_asks_next_missing_field(monkeypatch):
    from types import SimpleNamespace
    from app.services.ai_draft_service import AIDraftService

    draft = SimpleNamespace(
        draft_id="00000000-0000-4000-8000-000000000000",
        command="DRAFT_LEAVE",
        revision=1,
        typed_params={"leave_date": "2026-10-12", "leave_type_id": 11},
        missing_fields=["session", "reason"],
    )
    get_draft = AsyncMock(return_value=draft)
    update_draft = AsyncMock(return_value=draft)
    monkeypatch.setattr(AIDraftService, "get_draft", get_draft)
    monkeypatch.setattr(AIDraftService, "update_draft", update_draft)
    result = await AIService.process_chat(
        None, 7, AIChatRequest(message="buổi sáng", draft_id=draft.draft_id, draft_revision=1), {"EMPLOYEE"}, owner_user_account_id=9
    )
    assert result.draft_id == draft.draft_id
    assert result.reply == "Vui lòng cho biết lý do để đưa vào bản nháp."
    get_draft.assert_awaited_once_with(None, draft.draft_id, 9)
    update_draft.assert_awaited_once_with(None, draft.draft_id, 9, {"session": "MORNING"}, ["reason"])


@pytest.fixture
def context(monkeypatch):
    from app.services.ai_employee_tool import AIEmployeeTool
    value = {"today": "2026-10-05", "remaining_leave_days": "0.50", "present_days": 2, "incomplete_dates": ["2026-10-02"], "leave_types": [{"id": 11, "code": "ANNUAL", "name": "Phép năm"}, {"id": 12, "code": "UNPAID", "name": "Không lương"}]}
    monkeypatch.setattr(AIService, "get_employee_context", AsyncMock(return_value=value))
    monkeypatch.setattr(AIEmployeeTool, "get_context", AsyncMock(return_value=value))
    monkeypatch.setattr("app.services.ai_service.business_today", lambda: date(2026, 10, 5))
    return value


@pytest.mark.asyncio
async def test_draft_checks_balance_and_does_not_switch_type(context):
    result = await AIService.process_chat(None, 7, AIChatRequest(message="xin nghỉ ngày mai"), {"EMPLOYEE"})
    assert result.action.action_type == "NAVIGATE"
    assert "không tự chuyển" in result.reply
    result = await AIService.process_chat(None, 7, AIChatRequest(message="xin nghỉ sáng mai"), {"EMPLOYEE"})
    assert result.action.data["leave_type_id"] == 11
    assert result.action.data["session"] == "MORNING"
    context["remaining_leave_days"] = "2.00"
    result = await AIService.process_chat(None, 7, AIChatRequest(message="xin nghỉ hôm nay"), {"EMPLOYEE"})
    assert result.action.data["leave_type_id"] == 11


@pytest.mark.asyncio
async def test_fix_does_not_invent_date_or_time(context):
    result = await AIService.process_chat(None, 7, AIChatRequest(message="quên check-out hôm qua"), {"EMPLOYEE"})
    assert result.action.data["work_date"] == "2026-10-04"
    assert "requested_at" not in result.action.data
    result = await AIService.process_chat(None, 7, AIChatRequest(message="quên check-out"), {"EMPLOYEE"})
    assert result.action is None
    result = await AIService.process_chat(None, 7, AIChatRequest(message="hôm qua 02/10 quên quẹt thẻ lúc về 17h"), {"EMPLOYEE"})
    assert result.action.data["requested_time"] == "17:00:00"
    assert result.action.data["event_type"] == "CHECK_OUT"


@pytest.mark.asyncio
async def test_missing_attendance_question_is_lookup_not_draft(context):
    result = await AIService.process_chat(None, 7, AIChatRequest(message="Tôi có ngày nào quên check-out không?"), {"EMPLOYEE"})
    assert result.action.action_type == "SHOW_DATA"
    assert result.action.data["incomplete"] == ["2026-10-02"]


@pytest.mark.asyncio
async def test_knowledge_query_filters_role_before_retrieval(context):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
    result = await AIService.process_chat(db, 7, AIChatRequest(message="giờ làm việc"), {"EMPLOYEE"})
    query = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "minimum_role IN ('EMPLOYEE')" in query
    assert "PUBLISHED" in query
    assert result.answer_mode == "OUT_OF_SCOPE" and result.sources == []


@pytest.mark.asyncio
async def test_unknown_role_is_rejected_before_any_database_query():
    from fastapi import HTTPException
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await AIService.process_chat(db, 7, AIChatRequest(message="xem số dư phép"), {"UNKNOWN"})
    assert exc.value.status_code == 403
    db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Tôi còn bao nhiêu phép của Nguyễn An?", "Xem công E3", "Tạo báo cáo toàn công ty"])
async def test_foreign_or_company_requests_are_denied_before_lookup(message, context):
    db = AsyncMock()
    result = await AIService.process_chat(db, 7, AIChatRequest(message=message), {"EMPLOYEE"})
    assert result.action is None
    assert "Bạn không có quyền" in result.reply
    db.execute.assert_not_called()
    AIService.get_employee_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_report_fallback_returns_the_actual_created_run(context, monkeypatch):
    from app.services.report_run_service import ReportRunService
    from app.services.ai_draft_service import AIDraftService
    from unittest.mock import Mock
    account = SimpleNamespace(user_account_id=9, employee_id=7, role_assignments=user("MANAGER").role_assignments)
    create = AsyncMock(return_value={"run": SimpleNamespace(run_id="a" * 32)})
    monkeypatch.setattr(ReportRunService, "create", create)
    monkeypatch.setattr(AIDraftService, "mark_ready", AsyncMock())
    db = AsyncMock()
    db.add = Mock()
    result = await AIService.process_chat(db, 7, AIChatRequest(message="Tạo báo cáo công của tôi tháng này"), {"MANAGER"}, owner_user_account_id=9, authenticated_user=account)
    assert result.action.data["run_id"] == "a" * 32
    assert create.await_args.args[1] is account
    assert create.await_args.args[2].scope == "SELF"


@pytest.mark.asyncio
async def test_free_prompt_does_not_load_personal_context(context):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
    result = await AIService.process_chat(db, 7, AIChatRequest(message="giải thích chủ đề ngoài HR"), {"EMPLOYEE"})
    assert result.message_code == "CLARIFICATION_REQUIRED"
    assert AIService.get_employee_context.await_count == 0


@pytest.mark.asyncio
async def test_headcount_chat_shortcut_is_denied_for_employee(context):
    from fastapi import HTTPException
    denied = await AIService.process_chat(
        None, 7, AIChatRequest(message="Mở thống kê headcount hiện tại"), {"EMPLOYEE"})
    assert denied.action is None and denied.answer_mode == "OUT_OF_SCOPE"
    assert "Bạn không có quyền" in denied.reply


@pytest.mark.asyncio
async def test_unrelated_followup_does_not_inherit_policy_sources(context):
    from app.schemas.ai import AIChatMessage
    doc = SimpleNamespace(document_id=1, title="Giờ làm việc", content="Giờ làm việc từ 08:00 đến 17:00.", source_url=None, status="PUBLISHED")
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [{
        "document_id": 1, "title": doc.title, "content": doc.content, "source_url": None,
        "status": "PUBLISHED", "document_status": "PUBLISHED", "is_answerable": True,
    }]))
    request = AIChatRequest(message="lập trình python", conversation_history=[AIChatMessage(role="user", content="giờ làm việc")])
    result = await AIService.process_chat(db, 7, request, {"EMPLOYEE"}, owner_user_account_id=7)
    assert result.message_code == "CLARIFICATION_REQUIRED" and result.sources == []


@pytest.mark.asyncio
async def test_gemini_timeout_and_ungrounded_output_fall_back(context, monkeypatch):
    from app.services import ai_service as module
    # The mocked operation is policy generation, not semantic planning.
    monkeypatch.setattr(module.settings, "GEMINI_SEMANTIC_ENABLED", False)
    from app.schemas.ai import AIChatMessage
    doc = SimpleNamespace(document_id=1, title="Giờ làm việc", content="Giờ làm việc từ 08:00 đến 17:00.", source_url=None, status="PUBLISHED")
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [{
        "document_id": 1, "title": doc.title, "content": doc.content, "source_url": None,
        "status": "PUBLISHED", "document_status": "PUBLISHED", "is_answerable": True,
    }]))
    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(module.settings, "GEMINI_API_KEY", "test-key")
    from app.ai.providers.gemini import ProviderFailure
    generate = AsyncMock(side_effect=ProviderFailure("PROVIDER_TIMEOUT"))
    monkeypatch.setattr(module, "generate_json", generate)
    request = AIChatRequest(message="giải thích giờ làm việc", conversation_history=[AIChatMessage(role="user", content="employee id 12345 private history")])
    result = await AIService.process_chat(db, 7, request, {"EMPLOYEE"}, owner_user_account_id=7)
    assert result.answer_mode == "RAG" and result.sources[0].document_id == 1
    assert generate.call_count == 1
    assert generate.call_args.kwargs["max_tokens"] == 1536
    provider_prompt = generate.call_args.args[1]
    assert '"personal_context"' not in provider_prompt
    assert "private history" not in provider_prompt and "12345" not in provider_prompt
    from app.schemas.ai import AIGroundedAnswer
    generate.side_effect = None
    generate.return_value = AIGroundedAnswer(statements=[{"source": 1, "text": "ngoài kiến thức", "evidence": "Không nằm trong nguồn"}])
    result = await AIService.process_chat(db, 7, request, {"EMPLOYEE"}, owner_user_account_id=7)
    assert result.answer_mode == "RAG" and "ngoài kiến thức" not in result.reply


@pytest.mark.asyncio
async def test_free_intent_classifier_uses_closed_schema_and_redacts_personal_fields(monkeypatch):
    from app.services import ai_service as module

    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(module.settings, "GEMINI_API_KEY", "test-key")
    from app.schemas.ai import AIIntentResult
    generate = AsyncMock(return_value=AIIntentResult(intent="DRAFT_LEAVE"))
    monkeypatch.setattr(module, "generate_json", generate)
    result = await AIService._classify_free_intent("Xin nghỉ 2026-10-12, tên tôi là Nguyễn An, email ann@example.com, lý do: khám bệnh")
    assert result == "DRAFT_LEAVE"
    assert generate.call_count == 1
    assert generate.call_args.kwargs["max_tokens"] == 512
    assert generate.call_args.args[3] is AIIntentResult
    assert generate.call_args.kwargs["timeout"] == module.settings.GEMINI_INTENT_TIMEOUT_SECONDS
    payload = generate.call_args.args[1]
    assert "2026-10-12" not in payload
    assert "Nguyễn An" not in payload
    assert "ann@example.com" not in payload
    assert "khám bệnh" not in payload


@pytest.mark.asyncio
async def test_free_hr_prompt_without_gemini_returns_stable_unavailable_code(monkeypatch):
    from fastapi import HTTPException
    from app.services import ai_service as module

    monkeypatch.setattr(module.settings, "GEMINI_API_KEY", "")
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
    response = await AIService.process_chat(db, 7, AIChatRequest(message="Có thể xem giúp số phép năm của tôi không?"), {"EMPLOYEE"})
    assert response.message_code == "CLARIFICATION_REQUIRED"
    assert response.status == "NEEDS_INPUT"


@pytest.mark.asyncio
async def test_gemini_disabled_even_when_key_is_configured(monkeypatch):
    from fastapi import HTTPException
    from app.services import ai_service as module
    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", False)
    monkeypatch.setattr(module.settings, "GEMINI_API_KEY", "test-key")
    from app.ai.providers import gemini
    provider = AsyncMock()
    monkeypatch.setattr(gemini.genai, "Client", provider)
    with pytest.raises(HTTPException) as exc:
        await AIService._classify_free_intent("help with HR")
    assert exc.value.detail == "AI_UNAVAILABLE"
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_rag_reply_is_concise_and_can_be_sent_back_as_followup_history(monkeypatch):
    from app.services import ai_service as module
    from app.schemas.ai import AIChatMessage
    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", False)
    docs = [{"document_id": i, "title": "Gio lam viec " + "x" * 180,
             "content": "Gio lam viec " + "x" * 1187, "status": "PUBLISHED",
             "source_url": None, "is_answerable": True} for i in range(1, 4)]
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: docs))
    result = await AIService.process_chat(db, 7, AIChatRequest(message="gio lam viec"), {"EMPLOYEE"})
    assert result.answer_mode == "RAG" and len(result.reply) < 1600
    request = AIChatRequest(message="noi ro hon", conversation_history=[
        AIChatMessage(role="user", content="gio lam viec"),
        AIChatMessage(role="assistant", content=result.reply),
    ])
    followup = await AIService.process_chat(db, 7, request, {"EMPLOYEE"})
    assert followup.sources and followup.answer_mode == "RAG"


@pytest.mark.parametrize("message, expected", [
    ("10 08 2025", "2025-08-10"),
    ("10-08-2025", "2025-08-10"),
    ("10.08.2025", "2025-08-10"),
    ("2025 08 10", "2025-08-10"),
    ("ngày 10 tháng 8 năm 2025", "2025-08-10"),
    ("mùng 10 tháng 8", "2026-08-10"),
    ("ngày kia", "2026-10-07"),
    ("3 ngày nữa", "2026-10-08"),
    ("31 02 2026", None),
    ("2026-02-31", None),
])
def test_chat_accepts_flexible_dates_without_provider(message, expected):
    assert requested_date(message, date(2026, 10, 5)) == expected


@pytest.mark.asyncio
async def test_space_separated_date_advances_owner_bound_draft(monkeypatch):
    from app.services.ai_draft_service import AIDraftService
    draft = SimpleNamespace(draft_id="00000000-0000-4000-8000-000000000000",
                            command="DRAFT_LEAVE", revision=1, typed_params={},
                            missing_fields=["leave_date", "session", "leave_type_id", "reason"])
    monkeypatch.setattr(AIDraftService, "get_draft", AsyncMock(return_value=draft))
    update = AsyncMock(return_value=draft)
    monkeypatch.setattr(AIDraftService, "update_draft", update)
    result = await AIService.process_chat(
        None, 7, AIChatRequest(message="10 08 2025", draft_id=draft.draft_id, draft_revision=1),
        {"EMPLOYEE"}, owner_user_account_id=9,
    )
    assert result.missing_fields == ["session", "leave_type_id", "reason"]
    assert "10/08/2025" in result.reply
    update.assert_awaited_once_with(None, draft.draft_id, 9, {"leave_date": "2025-08-10"},
                                   ["session", "leave_type_id", "reason"])



def test_unrecognized_date_answer_does_not_become_reason():
    assert AIService._draft_answers("DRAFT_LEAVE", "not sure",
        ["leave_date", "session", "reason"]) == {}
    assert AIService._draft_answers("DRAFT_LEAVE", "family appointment",
        ["reason"]) == {"reason": "family appointment"}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload, expected", [
    ('{"date":"2026-09-28"}', "2026-09-28"),
    ('{"date":null}', None),
    ('{"date":"2026-02-31"}', None),
    ('{"date":"2026-09-28","reason":"injected"}', None),
])
async def test_gemini_date_parser_has_closed_output_and_minimal_context(monkeypatch, payload, expected):
    from app.ai.parsing import dates
    from app.ai.providers.gemini import ProviderFailure
    from app.schemas.ai import AIChatDateResult
    from pydantic import ValidationError
    from app.services import ai_service as module
    monkeypatch.setattr(module, "business_today", lambda: date(2026, 10, 5))
    async def fake_generate(*args, **kwargs):
        try:
            return AIChatDateResult.model_validate_json(payload)
        except ValidationError as exc:
            raise ProviderFailure("SCHEMA_VALIDATION_FAILED") from exc
    generate = AsyncMock(side_effect=fake_generate)
    monkeypatch.setattr(dates, "generate_json", generate)
    result = await AIService._interpret_chat_date("ngay thu hai cua tuan lien truoc, private@example.test, ly do kham benh")
    assert result == expected
    prompt = generate.call_args.args[1]
    assert "2026-10-05" in prompt and "thu hai cua tuan lien truoc" in prompt
    assert "private" not in prompt and "kham" not in prompt
    assert generate.call_args.kwargs["timeout"] == 5


@pytest.mark.asyncio
async def test_gemini_date_failure_and_disabled_provider_keep_date_missing(monkeypatch):
    from app.services import ai_service as module
    from app.ai.parsing import dates
    from app.ai.providers.gemini import ProviderFailure
    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", False)
    assert await AIService._interpret_chat_date("thu hai cua tuan lien truoc") is None
    monkeypatch.setattr(module.settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(module.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(dates, "generate_json", AsyncMock(side_effect=ProviderFailure("PROVIDER_TIMEOUT")))
    assert await AIService._interpret_chat_date("thu hai cua tuan lien truoc") is None


@pytest.mark.asyncio
async def test_gemini_date_is_applied_only_to_date_field_in_owner_draft(monkeypatch):
    from app.services.ai_draft_service import AIDraftService
    draft = SimpleNamespace(draft_id="00000000-0000-4000-8000-000000000000",
                            command="DRAFT_LEAVE", revision=1, typed_params={},
                            missing_fields=["leave_date", "session", "leave_type_id", "reason"])
    monkeypatch.setattr(AIDraftService, "get_draft", AsyncMock(return_value=draft))
    update = AsyncMock(return_value=draft)
    monkeypatch.setattr(AIDraftService, "update_draft", update)
    from app.ai.parsing import dates
    from app.schemas.ai import AIChatDateResult
    interpret = AsyncMock(return_value=AIChatDateResult(date="2026-09-28"))
    monkeypatch.setattr(dates, "generate_json", interpret)
    result = await AIService.process_chat(None, 7,
        AIChatRequest(message="thu hai cua tuan lien truoc", draft_id=draft.draft_id, draft_revision=1),
        {"EMPLOYEE"}, owner_user_account_id=9)
    assert result.missing_fields[0] == "session" and "28/09/2026" in result.reply
    assert update.call_args.args[3] == {"leave_date": "2026-09-28"}
    interpret.assert_awaited_once()


@pytest.mark.parametrize("text,expected", [
    ("thứ 3 tuần sau", "2026-10-13"),
    ("thứ 2 tuần trước", "2026-09-28"),
    ("thứ 6 tuần này", "2026-10-09"),
    ("ngày 10 tháng sau", "2026-11-10"),
    ("ngày 31 tháng sau", None),
])
def test_weekday_numbers_and_relative_months(text, expected):
    assert requested_date(text, date(2026, 10, 5)) == expected


@pytest.mark.asyncio
async def test_initial_leave_request_keeps_date_and_type_and_combines_remaining_questions(monkeypatch):
    monkeypatch.setattr(AIService, "_leave_preflight", AsyncMock(return_value=None))
    from app.services import ai_service as module
    from app.services.ai_draft_service import AIDraftService
    monkeypatch.setattr(module, "business_today", lambda: date(2026, 10, 5))
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [
        SimpleNamespace(leave_code="ANNUAL", leave_type_id=11)]))
    create = AsyncMock(return_value=SimpleNamespace(draft_id="00000000-0000-4000-8000-000000000000", revision=1))
    monkeypatch.setattr(AIDraftService, "create_draft", create)
    result = await AIService.process_chat(db, 7,
        AIChatRequest(message="tạo đơn nghỉ phép năm cho tôi vào thứ 3 tuần sau"),
        {"EMPLOYEE"}, owner_user_account_id=9)
    assert create.call_args.args[3] == {"leave_date": "2026-10-13", "leave_type_id": 11}
    assert result.missing_fields == ["session", "reason"]
    assert "13/10/2026" in result.reply and "Phép năm" in result.reply
    answers = AIService._draft_answers("DRAFT_LEAVE", "cả ngày vì việc gia đình", result.missing_fields)
    assert answers == {"session": "FULL_DAY", "reason": "việc gia đình"}


@pytest.mark.asyncio
async def test_initial_draft_uses_gemini_for_date_when_local_parser_has_no_answer(monkeypatch):
    from app.ai.parsing import dates
    from app.schemas.ai import AIChatDateResult
    interpret = AsyncMock(return_value=AIChatDateResult(date="2026-11-10"))
    monkeypatch.setattr(dates, "generate_json", interpret)
    answers = await AIService._draft_answers_with_date("DRAFT_LEAVE", "xin nghi ngay muoi thang sau")
    assert answers == {"leave_date": "2026-11-10"}
    interpret.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_explicit_calendar_date_is_not_repaired_by_gemini(monkeypatch):
    from app.services import ai_service as module
    monkeypatch.setattr(module, "business_today", lambda: date(2026, 10, 5))
    from app.ai.parsing import dates
    interpret = AsyncMock()
    monkeypatch.setattr(dates, "generate_json", interpret)
    assert await AIService._draft_answers_with_date("DRAFT_LEAVE", "ngay 31 thang sau", ["leave_date"]) == {}
    interpret.assert_not_called()


@pytest.mark.asyncio
async def test_leave_balance_response_links_only_authorized_published_policy(context):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [{
        "document_id":3, "title":"Chính sách nghỉ phép", "content":"Phép năm và nghỉ phép được kiểm tra theo số dư.",
        "status":"PUBLISHED", "version_id":5, "section_id":8, "section_code":"PAGE_1",
        "heading":"Phép năm", "page_start":1, "page_end":1, "is_answerable":True,
    }]))
    result = await AIService.process_chat(db, 7, AIChatRequest(message="tôi còn bao nhiêu ngày phép"), {"EMPLOYEE"})
    assert result.action.data["balance"] == "0.50"
    assert result.sources[0].viewer_path == "/knowledge/view/3?version=5&section=8"
    sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds":True}))
    assert "PUBLISHED" in sql and "EMPLOYEE" in sql
    assert "MANAGER" not in sql


@pytest.mark.asyncio
async def test_exhausted_annual_leave_is_flagged_before_remaining_questions(monkeypatch):
    from decimal import Decimal
    from app.services.ai_draft_service import AIDraftService
    db = AsyncMock()
    types = [SimpleNamespace(leave_type_id=11, leave_code="ANNUAL", leave_name="Phép năm"),
             SimpleNamespace(leave_type_id=12, leave_code="UNPAID", leave_name="Không lương")]
    db.execute.side_effect = [
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: types)),
        SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(remaining_days=Decimal("0"))),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: types)),
    ]
    create = AsyncMock(return_value=SimpleNamespace(draft_id="00000000-0000-4000-8000-000000000000",revision=1))
    monkeypatch.setattr(AIDraftService,"create_draft",create)
    result = await AIService.process_chat(db,7,AIChatRequest(message="tạo phép năm cho tôi ngày mai"),{"EMPLOYEE"},owner_user_account_id=9)
    assert result.message_code == "LEAVE_BALANCE_INSUFFICIENT"
    assert result.missing_fields[0] == "leave_type_id"
    assert [(x.value,x.label) for x in result.input_options] == [(12,"Không lương")]
    assert create.await_args.args[3]["leave_type_id"] == 11  # Never silently change user's choice.
    assert result.action is None


@pytest.mark.asyncio
async def test_personal_lookup_inside_draft_preserves_state_and_does_not_become_reason(context, monkeypatch):
    from app.services.ai_draft_service import AIDraftService
    draft = SimpleNamespace(draft_id="00000000-0000-4000-8000-000000000000", revision=2, missing_fields=["reason"])
    monkeypatch.setattr(AIDraftService,"get_draft",AsyncMock(return_value=draft))
    update = AsyncMock()
    monkeypatch.setattr(AIDraftService,"update_draft",update)
    result = await AIService.process_chat(None,7,AIChatRequest(message="tôi còn bao nhiêu ngày phép",
        draft_id=draft.draft_id,draft_revision=2),{"EMPLOYEE"},owner_user_account_id=9)
    assert result.action.data["balance"] == "0.50"
    assert result.draft_id == draft.draft_id and result.missing_fields == ["reason"]
    update.assert_not_called()


@pytest.mark.asyncio
async def test_personal_attendance_uses_requested_period(context):
    result = await AIService.process_chat(None,7,AIChatRequest(message="tình trạng chấm công của tôi tháng trước"),{"EMPLOYEE"})
    AIService.get_employee_context.assert_awaited_once_with(None,7,
        attendance_start=date(2026,9,1),attendance_end=date(2026,9,30))
    assert result.action.data["present_days"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "thông tin ngày nghỉ của tôi",
    "tôi còn bao nhiêu ngày nghỉ",
    "tôi còn mấy ngày phép",
    "thông tin ngày phép của mình",
])
async def test_personal_leave_wording_uses_db_tool_without_gemini(context, monkeypatch, message):
    classify = AsyncMock(side_effect=AssertionError("Known personal lookup must not require Gemini"))
    monkeypatch.setattr(AIService, "_classify_free_intent", classify)
    result = await AIService.process_chat(None,7,AIChatRequest(message=message),{"EMPLOYEE"})
    assert result.action.data == {"type":"LEAVE_BALANCE","balance":"0.50"}
    assert "Số dư phép năm" in result.reply
    classify.assert_not_called()


@pytest.mark.asyncio
async def test_day_off_alias_preserves_personal_identity_guards(monkeypatch):
    from fastapi import HTTPException
    db = AsyncMock()
    lookup = AsyncMock(side_effect=AssertionError("Unauthorized lookup"))
    monkeypatch.setattr(AIService, "get_employee_context", lookup)
    with pytest.raises(HTTPException) as exc:
        await AIService.process_chat(db,None,AIChatRequest(message="tôi còn bao nhiêu ngày nghỉ"),{"HR"})
    assert exc.value.status_code == 403
    result = await AIService.process_chat(db,7,AIChatRequest(message="thông tin ngày nghỉ của Nam"),{"EMPLOYEE"})
    assert result.action is None and result.answer_mode == "OUT_OF_SCOPE"
    lookup.assert_not_called()
    db.execute.assert_not_called()


def test_personal_leave_alias_does_not_replace_policy_or_report():
    from app.services.ai_service import is_leave_balance_query
    assert not is_leave_balance_query("thong tin chinh sach ngay nghi cua toi")
    assert not is_leave_balance_query("bao cao phep con lai cua toi")


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["DRAFT_LEAVE", "DRAFT_FIX"])
@pytest.mark.parametrize("prefix", ["", "lý do: ", "lý do ", "vì ", "bởi vì ", "do ", "ly do: ", "vi "])
async def test_reason_turn_preserves_selected_draft_fields(monkeypatch, command, prefix):
    reason = "chăm người thân sau ca phẫu thuật buổi sáng ngày mai lúc 8h check-in"
    interpret = AsyncMock(side_effect=AssertionError("Reason text must not trigger date interpretation"))
    monkeypatch.setattr(AIService, "_interpret_chat_date", interpret)
    answers = await AIService._draft_answers_with_date(command, prefix + reason, ["reason"])
    assert answers == {"reason": reason}
    interpret.assert_not_called()


def test_combined_session_and_reason_uses_only_request_text():
    assert AIService._draft_answers(
        "DRAFT_LEAVE", "cả ngày vì chăm người thân ngày mai buổi sáng", ["session", "reason"]
    ) == {"session": "FULL_DAY", "reason": "chăm người thân ngày mai buổi sáng"}


@pytest.mark.asyncio
async def test_reason_turn_completes_leave_without_changing_date_or_session(context, monkeypatch):
    from app.services.ai_draft_service import AIDraftService
    draft = SimpleNamespace(
        draft_id="00000000-0000-4000-8000-000000000000", revision=1,
        command="DRAFT_LEAVE", missing_fields=["reason"],
        typed_params={"leave_date": "2026-10-12", "session": "AFTERNOON", "leave_type_id": 12},
    )
    monkeypatch.setattr(AIDraftService, "get_draft", AsyncMock(return_value=draft))
    update = AsyncMock(return_value=draft)
    monkeypatch.setattr(AIDraftService, "update_draft", update)
    monkeypatch.setattr(AIDraftService, "mark_ready", AsyncMock(return_value=draft))
    monkeypatch.setattr(AIService, "_leave_preflight", AsyncMock(return_value=None))
    reason = "chăm người thân sau ca phẫu thuật buổi sáng ngày mai"
    result = await AIService._continue_draft(None, 9, draft.draft_id, reason, 7, 1)
    assert result.action.action_type == "DRAFT_LEAVE"
    assert result.action.data == {**draft.typed_params, "reason": reason}
    assert update.await_args.args[3:] == ({"reason": reason}, [])


@pytest.mark.parametrize("message, expected", [
    ("Xin làm việc từ xa ngày mai", "REMOTE"),
    ("Xin WFH sáng mai", "REMOTE"),
    ("Tạo đơn Remote", "REMOTE"),
    ("Xin nghỉ phép năm vì hôm qua làm việc từ xa", "ANNUAL"),
])
def test_remote_type_detection_preserves_reason_boundary(message, expected):
    assert AIService._requested_leave_code(message) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Xin làm việc từ xa ngày mai", "Xin WFH sáng mai", "Tạo đơn Remote ngày mai"])
async def test_remote_draft_selects_active_type_without_annual_quota(monkeypatch, message):
    from decimal import Decimal
    from app.services.ai_draft_service import AIDraftService
    db = AsyncMock()
    remote = SimpleNamespace(leave_type_id=14, leave_code="REMOTE", leave_name="Remote")
    type_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [remote]))
    db.execute.side_effect = [type_result,
        SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(remaining_days=Decimal("0.00"))), type_result]
    create = AsyncMock(return_value=SimpleNamespace(draft_id="00000000-0000-4000-8000-000000000000", revision=1))
    monkeypatch.setattr(AIDraftService, "create_draft", create)
    result = await AIService.process_chat(db, 7, AIChatRequest(message=message), {"EMPLOYEE"}, owner_user_account_id=9)
    assert result.draft_id is not None and result.action is None
    assert result.message_code != "LEAVE_BALANCE_INSUFFICIENT"
    assert create.await_args.args[3]["leave_type_id"] == 14
    assert "reason" in result.missing_fields
    assert "Làm việc từ xa" in result.reply


@pytest.mark.asyncio
async def test_remote_policy_uses_authorized_knowledge_without_creating_request(monkeypatch):
    from app.services.ai_draft_service import AIDraftService
    monkeypatch.setattr("app.services.ai_service.settings.GEMINI_ENABLED", False)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [{
        "document_id": 3, "title": "Chính sách Remote", "status": "PUBLISHED",
        "content": "Làm việc từ xa (Remote) do quản lý trực tiếp duyệt và không trừ quỹ phép năm.",
        "version_id": 5, "section_id": 8, "section_code": "LEAVE.REQUEST",
        "heading": "Quy định Remote", "page_start": 3, "page_end": 3, "is_answerable": True,
    }]))
    create = AsyncMock(side_effect=AssertionError("Policy query must not create a request"))
    monkeypatch.setattr(AIDraftService, "create_draft", create)
    result = await AIService.process_chat(db, 7, AIChatRequest(message="Quy định làm việc từ xa Remote có trừ phép năm không?"), {"EMPLOYEE"}, owner_user_account_id=9)
    assert result.answer_mode == "RAG" and result.action is None and result.draft_id is None
    assert "không trừ quỹ phép năm" in result.reply
    assert result.sources[0].viewer_path == "/knowledge/view/3?version=5&section=8"
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_replayed_completed_leave_draft_returns_prepared_form_without_mutation(monkeypatch):
    from app.services.ai_draft_service import AIDraftService
    from app.schemas.ai import AIChatRequest
    values = {"leave_date": "2026-10-15", "session": "FULL_DAY",
              "leave_type_id": 2, "reason": "Việc cá nhân"}
    draft = SimpleNamespace(status="READY", command="DRAFT_LEAVE", typed_params=values)
    monkeypatch.setattr(AIService, "_draft_answers_with_date", AsyncMock(return_value=values))
    monkeypatch.setattr(AIService, "_leave_preflight", AsyncMock(return_value=None))
    monkeypatch.setattr(AIDraftService, "create_draft", AsyncMock(return_value=draft))
    continuation = AsyncMock(side_effect=AssertionError("READY draft must not be completed again"))
    monkeypatch.setattr(AIService, "_continue_draft", continuation)
    response = await AIService._start_draft(
        AsyncMock(), 7, 9, "DRAFT_LEAVE",
        AIChatRequest(message="Xin nghỉ ngày 15/10/2026 vì việc cá nhân"))
    assert response.action.action_type == "DRAFT_LEAVE"
    assert response.action.data["leave_date"] == values["leave_date"]
    continuation.assert_not_awaited()
