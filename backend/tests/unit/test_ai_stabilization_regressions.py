"""Regressions recorded before the AI stabilization changes."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ai_service import AIService, requested_date
from app.services.ai_access import allowed_knowledge_roles


def test_compound_first_monday_next_month():
    assert requested_date("Ngày thứ hai của tuần đầu tiên trong tháng tới", date(2026, 10, 13)) == "2026-11-02"


def test_known_admin_role_survives_unknown_additional_assignment():
    assert allowed_knowledge_roles({"ADMIN", "EXPERIMENTAL"}) == ["EMPLOYEE", "MANAGER", "HR", "ADMIN"]


@pytest.mark.asyncio
async def test_provider_failure_during_followup_preserves_draft_revision(monkeypatch):
    from app.ai.parsing import dates
    from app.ai.providers.gemini import ProviderFailure
    from app.schemas.ai import AIChatRequest
    from app.services.ai_draft_service import AIDraftService
    from app.services import ai_service

    draft = SimpleNamespace(draft_id="00000000-0000-4000-8000-000000000000", command="DRAFT_LEAVE",
                            revision=3, typed_params={}, missing_fields=["leave_date", "session", "leave_type_id", "reason"])
    monkeypatch.setattr(ai_service, "business_today", lambda: date(2026, 10, 13))
    monkeypatch.setattr(AIDraftService, "get_draft", AsyncMock(return_value=draft))
    update = AsyncMock()
    monkeypatch.setattr(AIDraftService, "update_draft", update)
    monkeypatch.setattr(dates, "generate_json", AsyncMock(side_effect=ProviderFailure("PROVIDER_TIMEOUT")))

    response = await AIService.process_chat(None, 7, AIChatRequest(
        message="ngày thứ hai của tuần liền kề", draft_id=draft.draft_id, draft_revision=3),
        {"EMPLOYEE"}, owner_user_account_id=9)
    assert response.draft_revision == 3 and response.missing_fields[0] == "leave_date"
    assert response.status == "UNAVAILABLE" and response.message_code == "AI_UNAVAILABLE", response.model_dump()
    update.assert_not_awaited()
