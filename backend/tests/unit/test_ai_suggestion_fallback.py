"""Production knowledge can support policy suggestions without demo document codes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.api.v1.endpoints import ai
from app.core.database import get_db


@pytest.mark.asyncio
async def test_published_readable_section_supports_policy_suggestion_without_demo_code(monkeypatch):
    app = FastAPI()
    app.include_router(ai.router, prefix="/ai")
    actor = SimpleNamespace(employee_id=7, user_account_id=9,
        role_assignments=[SimpleNamespace(role=SimpleNamespace(role_code="EMPLOYEE"))])
    db = AsyncMock()
    empty = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
    scope_rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    db.execute.side_effect = [scope_rows, empty, empty]
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[get_db] = lambda: db
    visible = [{
        "document_id": 31, "title": "Quy định nghỉ phép", "content":
            "Quy định nghỉ phép hằng năm như thế nào? Nhân viên xin nghỉ phép năm theo quy trình đã công bố.",
        "status": "PUBLISHED", "source_url": None, "version_id": 32,
        "section_id": 33, "section_code": "HR-POLICY-LEAVE", "heading": "Nghỉ phép năm",
        "page_start": 2, "page_end": 2, "is_answerable": True,
    }]
    monkeypatch.setattr(ai.AIKnowledgeTool, "documents", AsyncMock(return_value=visible))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ai/suggestions")
    assert response.status_code == 200
    suggestions = {item["suggestion_id"]: item for item in response.json()}
    assert suggestions["leave_policy"]["source"]["section_code"] == "HR-POLICY-LEAVE"
    assert suggestions["leave_policy"]["source"]["viewer_path"] == "/knowledge/view/31?version=32&section=33"
