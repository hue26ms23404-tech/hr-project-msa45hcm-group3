from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.ai import tool_registry
from app.schemas.ai import AIChatRequest, AIChatResponse


def context(roles, employee_id=7):
    return tool_registry.ToolContext(None, employee_id, 9, roles,
                                     AIChatRequest(message="tra cứu phép"), date(2026, 10, 13))


@pytest.mark.asyncio
async def test_registry_executes_only_known_authorized_intents(monkeypatch):
    original = tool_registry.TOOLS["LEAVE_BALANCE"]
    handler = AsyncMock(return_value=AIChatResponse(reply="ok"))
    monkeypatch.setitem(tool_registry.TOOLS, "LEAVE_BALANCE",
                        tool_registry.RegisteredTool(original.tool_id, original.input_schema,
                                                     original.capability, original.needs_employee, handler))
    assert await tool_registry.execute_registered("EXECUTE_SQL", context({"ADMIN"})) is None
    with pytest.raises(HTTPException):
        await tool_registry.execute_registered("LEAVE_BALANCE", context({"UNKNOWN"}))
    with pytest.raises(HTTPException):
        await tool_registry.execute_registered("LEAVE_BALANCE", context({"EMPLOYEE"}, None))
    result = await tool_registry.execute_registered("LEAVE_BALANCE", context({"ADMIN", "UNKNOWN"}))
    assert result.reply == "ok"
    handler.assert_awaited_once()
