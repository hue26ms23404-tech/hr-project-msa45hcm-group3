"""Owner-scoped persistence for multi-turn AI action drafts."""

from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4, uuid5, NAMESPACE_URL
from app.services.ai_request_security import current_request_id

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import AIChatDraft


DRAFT_TTL = timedelta(minutes=15)
COMMAND_FIELDS = {
    "DRAFT_LEAVE": frozenset({"leave_date", "session", "leave_type_id", "reason"}),
    "DRAFT_FIX": frozenset({"work_date", "event_type", "reason", "requested_time"}),
    "DRAFT_REPORT": frozenset({"kind", "start_date", "end_date", "scope", "department_id", "employee_id"}),
}
REQUIRED_FIELDS = {**COMMAND_FIELDS, "DRAFT_REPORT": frozenset({"kind", "start_date", "end_date", "scope"})}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AIDraftService:
    """Create and mutate drafts only in the authenticated user's namespace."""

    @staticmethod
    def _validate_command_and_params(command: str, params: dict) -> None:
        if command not in COMMAND_FIELDS:
            raise HTTPException(status_code=422, detail="AI_DRAFT_COMMAND_INVALID")
        if not isinstance(params, dict) or not set(params).issubset(COMMAND_FIELDS[command]):
            raise HTTPException(status_code=422, detail="AI_DRAFT_FIELD_INVALID")
        try:
            for key, value in params.items():
                if key in {"leave_type_id", "department_id", "employee_id"}:
                    if type(value) is not int or value <= 0:
                        raise ValueError()
                elif not isinstance(value, str):
                    raise ValueError()
                elif key in {"leave_date", "work_date", "start_date", "end_date"}:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                elif key == "requested_time":
                    if time.fromisoformat(value).isoformat() != value or len(value) != 8:
                        raise ValueError()
                elif key == "session" and value not in {"FULL_DAY", "MORNING", "AFTERNOON"}:
                    raise ValueError()
                elif key == "event_type" and value not in {"CHECK_IN", "CHECK_OUT"}:
                    raise ValueError()
                elif key == "reason" and (not value.strip() or len(value) > 1000):
                    raise ValueError()
                elif key == "kind":
                    from app.services.report_catalog import REPORT_NAMES
                    if value not in REPORT_NAMES:
                        raise ValueError()
                elif key == "scope" and value not in {"SELF", "DIRECT_REPORTS", "COMPANY"}:
                    raise ValueError()
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="AI_DRAFT_FIELD_INVALID") from exc

    @staticmethod
    def _validate_missing_fields(command: str, missing_fields: list[str]) -> None:
        if (
            not isinstance(missing_fields, list)
            or any(not isinstance(field, str) for field in missing_fields)
            or not set(missing_fields).issubset(COMMAND_FIELDS[command])
        ):
            raise HTTPException(status_code=422, detail="AI_DRAFT_FIELD_INVALID")

    @staticmethod
    async def create_draft(
        db: AsyncSession,
        owner_user_account_id: int,
        command: str,
        typed_params: dict,
        missing_fields: list[str] | None = None,
    ) -> AIChatDraft:
        AIDraftService._validate_command_and_params(command, typed_params)
        missing_fields = [] if missing_fields is None else missing_fields
        AIDraftService._validate_missing_fields(command, missing_fields)
        now = utc_now()
        operation_id = current_request_id()
        draft_id = (str(uuid5(NAMESPACE_URL, f"hr-ai-draft:{owner_user_account_id}:{operation_id}:{command}"))
                    if operation_id else str(uuid4()))
        if operation_id:
            existing = (await db.execute(select(AIChatDraft).where(
                AIChatDraft.draft_id == draft_id,
                AIChatDraft.owner_user_account_id == owner_user_account_id,
            ).with_for_update())).scalar_one_or_none()
            if existing is not None:
                await AIDraftService._ensure_unexpired(db, existing)
                if existing.status not in {"COLLECTING", "READY"}:
                    raise HTTPException(409, "AI_DRAFT_NOT_COLLECTING")
                if (existing.command != command or existing.typed_params != typed_params
                        or existing.missing_fields != missing_fields):
                    raise HTTPException(409, "AI_DRAFT_IDEMPOTENCY_CONFLICT")
                return existing
        draft = AIChatDraft(
            draft_id=draft_id,
            owner_user_account_id=owner_user_account_id,
            command=command,
            typed_params=dict(typed_params),
            missing_fields=list(missing_fields),
            revision=1,
            status="COLLECTING",
            expires_at=now + DRAFT_TTL,
        )
        db.add(draft)
        await db.flush()
        return draft

    @staticmethod
    async def _get_locked_draft(
        db: AsyncSession, draft_id: str, owner_user_account_id: int
    ) -> AIChatDraft:
        statement = (
            select(AIChatDraft)
            .where(
                AIChatDraft.draft_id == draft_id,
                AIChatDraft.owner_user_account_id == owner_user_account_id,
            )
            .with_for_update()
        )
        draft = (await db.execute(statement)).scalar_one_or_none()
        if draft is None:
            # Do not disclose whether another account owns this identifier.
            raise HTTPException(status_code=404, detail="AI_DRAFT_NOT_FOUND")
        return draft

    @staticmethod
    async def _ensure_unexpired(db: AsyncSession, draft: AIChatDraft) -> None:
        expires_at = draft.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= utc_now():
            draft.status = "EXPIRED"
            await db.flush()
            raise HTTPException(status_code=410, detail="AI_DRAFT_EXPIRED")

    @staticmethod
    async def get_draft(
        db: AsyncSession, draft_id: str, owner_user_account_id: int
    ) -> AIChatDraft:
        draft = await AIDraftService._get_locked_draft(
            db, draft_id, owner_user_account_id
        )
        await AIDraftService._ensure_unexpired(db, draft)
        return draft

    @staticmethod
    async def update_draft(
        db: AsyncSession,
        draft_id: str,
        owner_user_account_id: int,
        typed_params: dict,
        missing_fields: list[str] | None = None,
    ) -> AIChatDraft:
        draft = await AIDraftService._get_locked_draft(
            db, draft_id, owner_user_account_id
        )
        await AIDraftService._ensure_unexpired(db, draft)
        if draft.status != "COLLECTING":
            raise HTTPException(status_code=409, detail="AI_DRAFT_NOT_COLLECTING")
        AIDraftService._validate_command_and_params(draft.command, typed_params)
        merged_params = {**draft.typed_params, **typed_params}
        # Validate the merged data too, so malformed stored data cannot be
        # propagated back into an updated draft.
        AIDraftService._validate_command_and_params(draft.command, merged_params)
        if missing_fields is not None:
            AIDraftService._validate_missing_fields(draft.command, missing_fields)
            draft.missing_fields = list(missing_fields)
        draft.typed_params = merged_params
        draft.revision += 1
        draft.updated_at = utc_now()
        await db.flush()
        return draft

    @staticmethod
    async def mark_ready(
        db: AsyncSession, draft_id: str, owner_user_account_id: int
    ) -> AIChatDraft:
        draft = await AIDraftService._get_locked_draft(
            db, draft_id, owner_user_account_id
        )
        await AIDraftService._ensure_unexpired(db, draft)
        if draft.status != "COLLECTING" or draft.missing_fields or not REQUIRED_FIELDS.get(draft.command, frozenset()).issubset(draft.typed_params):
            raise HTTPException(status_code=409, detail="AI_DRAFT_NOT_READY")
        AIDraftService._validate_command_and_params(draft.command, draft.typed_params)
        draft.status = "READY"
        draft.revision += 1
        draft.updated_at = utc_now()
        await db.flush()
        return draft

    @staticmethod
    async def cancel_draft(
        db: AsyncSession, draft_id: str, owner_user_account_id: int
    ) -> AIChatDraft:
        draft = await AIDraftService._get_locked_draft(
            db, draft_id, owner_user_account_id
        )
        await AIDraftService._ensure_unexpired(db, draft)
        if draft.status != "COLLECTING":
            raise HTTPException(status_code=409, detail="AI_DRAFT_NOT_COLLECTING")
        draft.status = "CANCELLED"
        draft.revision += 1
        draft.updated_at = utc_now()
        await db.flush()
        return draft
