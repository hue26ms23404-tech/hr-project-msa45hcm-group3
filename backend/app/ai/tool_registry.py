"""Closed intent-to-tool mapping; model output never names Python callables or SQL."""

from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

from app.core.authorization import require_capability
from app.schemas.ai import AIChatAction, AIChatRequest, AIChatResponse
from app.services.ai_employee_tool import AIEmployeeTool
from app.services.ai_knowledge_tool import AIKnowledgeTool
from app.services.ai_report_interpreter import report_answers
from app.services.knowledge_retrieval import normalize


@dataclass(frozen=True)
class ToolContext:
    db: Any
    employee_id: int | None
    owner_user_account_id: int | None
    roles: set[str]
    request: AIChatRequest
    today: date


@dataclass(frozen=True)
class RegisteredTool:
    tool_id: str
    input_schema: type[AIChatRequest]
    capability: str
    needs_employee: bool
    handler: Callable[[ToolContext], Awaitable[AIChatResponse]]


async def _leave_balance(ctx: ToolContext) -> AIChatResponse:
    result = await AIEmployeeTool.get_context(ctx.db, ctx.employee_id, ctx.today)
    balance = result["remaining_leave_days"]
    reply = f"Số dư phép năm {ctx.today.year} của bạn: {balance} ngày." if balance is not None else "Chưa có dữ liệu số dư phép năm của bạn."
    sources = await AIKnowledgeTool.sources(ctx.db, ctx.roles, ctx.today, "chính sách phép năm nghỉ phép")
    return AIChatResponse(reply=reply, sources=sources,
                          action=AIChatAction(action_type="SHOW_DATA", data={"type": "LEAVE_BALANCE", "balance": balance}))


async def _attendance_summary(ctx: ToolContext) -> AIChatResponse:
    period = report_answers(ctx.request.message, ctx.today)
    start = date.fromisoformat(period["start_date"]) if period.get("start_date") else ctx.today.replace(day=1)
    end = date.fromisoformat(period["end_date"]) if period.get("end_date") else ctx.today
    if "hom nay" in normalize(ctx.request.message):
        start = end = ctx.today
    if end < start or start.year != end.year:
        raise HTTPException(422, "INVALID_INPUT")
    result = await AIEmployeeTool.get_context(ctx.db, ctx.employee_id, ctx.today,
                                              attendance_start=start, attendance_end=end)
    incomplete = result["incomplete_dates"]
    sources = await AIKnowledgeTool.sources(ctx.db, ctx.roles, ctx.today, "quy định chấm công")
    return AIChatResponse(
        reply=f"Từ {start:%d/%m/%Y} đến {end:%d/%m/%Y}: {result['present_days']} ngày đủ lượt công; các ngày thiếu lượt: {', '.join(incomplete) or 'không có'}. Đây là dữ liệu đã ghi nhận, chưa phải kết quả chốt công.",
        action=AIChatAction(action_type="SHOW_DATA", data={"type": "ATTENDANCE_SUMMARY",
                                                       "present_days": result["present_days"], "incomplete": incomplete}), sources=sources)


async def _start_leave(ctx: ToolContext) -> AIChatResponse:
    from app.services.ai_service import AIService
    if ctx.owner_user_account_id is None or ctx.db is None:
        raise HTTPException(403, "PERMISSION_DENIED")
    return await AIService._start_draft(ctx.db, ctx.employee_id, ctx.owner_user_account_id, "DRAFT_LEAVE", ctx.request)


async def _start_fix(ctx: ToolContext) -> AIChatResponse:
    from app.services.ai_service import AIService
    if ctx.owner_user_account_id is None or ctx.db is None:
        raise HTTPException(403, "PERMISSION_DENIED")
    return await AIService._start_draft(ctx.db, ctx.employee_id, ctx.owner_user_account_id, "DRAFT_FIX", ctx.request)


async def _open_knowledge(ctx: ToolContext) -> AIChatResponse:
    return AIChatResponse(reply="Mở màn hình quản lý tài liệu nội quy.",
                          action=AIChatAction(action_type="OPEN_KNOWLEDGE", data={"path": "/knowledge"}))


TOOLS = {
    "LEAVE_BALANCE": RegisteredTool("leave.read_self", AIChatRequest, "leave.read_self", True, _leave_balance),
    "ATTENDANCE_SUMMARY": RegisteredTool("attendance.read_self", AIChatRequest, "attendance.read_self", True, _attendance_summary),
    "DRAFT_LEAVE": RegisteredTool("leave.draft_self", AIChatRequest, "leave.draft_self", True, _start_leave),
    "DRAFT_FIX": RegisteredTool("attendance.draft_fix_self", AIChatRequest, "attendance.draft_fix_self", True, _start_fix),
    "OPEN_KNOWLEDGE": RegisteredTool("knowledge.manage", AIChatRequest, "knowledge.manage", False, _open_knowledge),
}


async def execute_registered(intent: str, ctx: ToolContext) -> AIChatResponse | None:
    tool = TOOLS.get(intent)
    if tool is None:
        return None
    require_capability(ctx.roles, tool.capability)
    if tool.needs_employee and ctx.employee_id is None:
        raise HTTPException(403, "PERMISSION_DENIED")
    return await tool.handler(ctx)
