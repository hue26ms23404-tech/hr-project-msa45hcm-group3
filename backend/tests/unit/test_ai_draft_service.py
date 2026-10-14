from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.services import ai_draft_service as module
from app.services.ai_draft_service import AIDraftService


def fake_db(result=None):
    db = SimpleNamespace(add=Mock(), flush=AsyncMock(), execute=AsyncMock())
    db.execute.return_value = SimpleNamespace(
        scalar_one_or_none=lambda: result
    )
    return db


@pytest.mark.asyncio
async def test_create_assigns_uuid_owner_and_utc_ttl(monkeypatch):
    now = module.utc_now()
    monkeypatch.setattr(module, "utc_now", lambda: now)
    db = fake_db()

    draft = await AIDraftService.create_draft(
        db, 42, "DRAFT_LEAVE", {"leave_date": "2026-10-08"}, ["session"]
    )

    UUID(draft.draft_id)
    assert draft.owner_user_account_id == 42
    assert draft.expires_at == now + timedelta(minutes=15)
    assert draft.expires_at.utcoffset() == timedelta(0)
    assert draft.revision == 1 and draft.status == "COLLECTING"
    db.add.assert_called_once_with(draft)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_scopes_by_owner_and_locks_row():
    draft = SimpleNamespace(expires_at=module.utc_now() + timedelta(minutes=5))
    db = fake_db(draft)

    assert await AIDraftService.get_draft(db, "opaque-id", 42) is draft

    sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "owner_user_account_id =" in sql
    assert "draft_id =" in sql
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_foreign_or_unknown_draft_is_hidden_as_not_found():
    db = fake_db(None)

    with pytest.raises(HTTPException) as exc:
        await AIDraftService.get_draft(db, "foreign-id", 99)

    assert exc.value.status_code == 404
    assert exc.value.detail == "AI_DRAFT_NOT_FOUND"


@pytest.mark.asyncio
async def test_expired_draft_is_marked_and_returns_stable_error():
    draft = SimpleNamespace(
        expires_at=module.utc_now() - timedelta(seconds=1), status="COLLECTING"
    )
    db = fake_db(draft)

    with pytest.raises(HTTPException) as exc:
        await AIDraftService.get_draft(db, "draft-id", 42)

    assert (exc.value.status_code, exc.value.detail) == (410, "AI_DRAFT_EXPIRED")
    assert draft.status == "EXPIRED"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_rejects_unallowlisted_keys_and_increments_revision():
    draft = SimpleNamespace(
        expires_at=module.utc_now() + timedelta(minutes=5),
        status="COLLECTING",
        command="DRAFT_FIX",
        typed_params={"work_date": "2026-10-08"},
        missing_fields=["event_type"],
        revision=3,
    )
    db = fake_db(draft)

    with pytest.raises(HTTPException) as exc:
        await AIDraftService.update_draft(db, "draft-id", 42, {"employee_id": 900})
    assert exc.value.status_code == 422
    assert exc.value.detail == "AI_DRAFT_FIELD_INVALID"
    db.flush.assert_not_awaited()

    updated = await AIDraftService.update_draft(
        db, "draft-id", 42, {"event_type": "CHECK_OUT"}, []
    )
    assert updated.typed_params == {
        "work_date": "2026-10-08",
        "event_type": "CHECK_OUT",
    }
    assert updated.missing_fields == []
    assert updated.revision == 4
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_requires_collecting_status():
    draft = SimpleNamespace(
        expires_at=module.utc_now() + timedelta(minutes=5),
        status="READY",
        command="DRAFT_LEAVE",
        typed_params={},
        missing_fields=[],
        revision=1,
    )
    db = fake_db(draft)

    with pytest.raises(HTTPException) as exc:
        await AIDraftService.update_draft(db, "draft-id", 42, {"reason": "medical"})

    assert exc.value.status_code == 409
    assert exc.value.detail == "AI_DRAFT_NOT_COLLECTING"
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_replayed_client_operation_reuses_the_original_draft():
    from app.services.ai_request_security import request_context
    from app.services.ai_draft_service import AIDraftService
    from types import SimpleNamespace
    from unittest.mock import Mock
    from uuid import uuid5, NAMESPACE_URL
    db = AsyncMock()
    db.add = Mock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    key = "chat:9:" + "a" * 32
    with request_context(key):
        first = await AIDraftService.create_draft(db, 9, "DRAFT_LEAVE", {}, ["leave_date"])
        assert first.draft_id == str(uuid5(NAMESPACE_URL, f"hr-ai-draft:9:{key}:DRAFT_LEAVE"))
        db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: first)
        replay = await AIDraftService.create_draft(db, 9, "DRAFT_LEAVE", {}, ["leave_date"])
        assert replay is first
        db.add.assert_called_once()
        with pytest.raises(HTTPException) as conflict:
            await AIDraftService.create_draft(db, 9, "DRAFT_LEAVE",
                {"leave_date": "2026-10-15"}, [])
        assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_replayed_expired_draft_does_not_return_stale_form(monkeypatch):
    from app.services.ai_request_security import request_context
    from datetime import timedelta
    from types import SimpleNamespace
    existing = SimpleNamespace(
        draft_id="a" * 36, owner_user_account_id=9, status="READY",
        expires_at=module.utc_now() - timedelta(seconds=1),
        command="DRAFT_LEAVE", typed_params={}, missing_fields=[],
    )
    db = fake_db(existing)
    with request_context("chat:9:" + "b" * 32):
        with pytest.raises(HTTPException) as exc:
            await AIDraftService.create_draft(db, 9, "DRAFT_LEAVE", {}, [])
    assert exc.value.status_code == 410
    assert existing.status == "EXPIRED"
