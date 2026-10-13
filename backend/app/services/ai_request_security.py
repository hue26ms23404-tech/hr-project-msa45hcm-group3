"""Durable per-user request budgets and structured AI request auditing."""

import hashlib
import math
import re
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from uuid import uuid4
from datetime import timedelta
from enum import Enum

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import AsyncSessionLocal
from app.models.ai_security import AIRequestEvent


RATE_LIMITS_PER_MINUTE = {"CHAT": 20, "REPORT": 5}
CHANNELS = frozenset({"CHAT", "REPORT", "UPLOAD", "KNOWLEDGE"})
ACTIONS = frozenset(
    {
        "BUDGET_REQUEST",
        "CHAT",
        "DRAFT_CREATE",
        "DRAFT_UPDATE",
        "DRAFT_CANCEL",
        "REPORT_CREATE",
        "REPORT_DOWNLOAD",
        "REPORT_ANALYSIS",
        "UPLOAD",
        "PUBLISH",
        "KNOWLEDGE_SEARCH",
    }
)
SCOPES = frozenset({"NONE", "SELF", "DIRECT_REPORTS", "COMPANY"})
OUTCOMES = frozenset(
    {"ALLOWED", "SUCCESS", "DENIED", "RATE_LIMITED", "FAILED", "REJECTED", "ERROR"}
)
_COMMAND_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,49}$")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
_request_id = ContextVar("ai_request_id", default=None)


def current_request_id() -> str | None:
    """Return the current audited request ID without creating a new one."""
    return _request_id.get()


@contextmanager
def request_context(request_id):
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


async def _set_transaction_timeouts(db: AsyncSession) -> None:
    await db.execute(text("SET LOCAL lock_timeout = '5s'"))
    await db.execute(text("SET LOCAL statement_timeout = '10s'"))


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail="DATA_SERVICE_UNAVAILABLE")


def _enum_value(value) -> str:
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="AI_AUDIT_VALUE_INVALID")
    return value.upper()


def _validate_request_id(request_id: str) -> None:
    if not isinstance(request_id, str) or not _REQUEST_ID_PATTERN.fullmatch(request_id):
        raise HTTPException(status_code=422, detail="AI_AUDIT_VALUE_INVALID")


def _advisory_lock_key(user_id: int, category: str) -> int:
    identity = f"hr-ai-budget:{category}:{user_id}".encode("ascii")
    return int.from_bytes(hashlib.blake2b(identity, digest_size=8).digest(), "big", signed=True)


async def consume_budget(
    db: AsyncSession, user_id: int, category: str, request_id: str
) -> None:
    """Reserve a rolling-minute budget slot, raising 429 when it is exhausted.

    Security rows commit in their own session so the request handler can roll
    back its business transaction without erasing the consumed budget slot.
    The caller's session parameter keeps this helper consistent with service
    call sites; it is intentionally not used for security persistence.
    """
    del db
    category = _enum_value(category)
    _validate_request_id(request_id)
    limit = RATE_LIMITS_PER_MINUTE.get(category)
    if limit is None:
        raise HTTPException(status_code=422, detail="AI_RATE_LIMIT_CATEGORY_INVALID")

    rate_limited = False
    retry_after = 1
    try:
        async with AsyncSessionLocal() as security_db:
            await _set_transaction_timeouts(security_db)
            lock_key = _advisory_lock_key(user_id, category)
            await security_db.execute(select(func.pg_advisory_xact_lock(lock_key)))
            # clock_timestamp is evaluated after the advisory lock is acquired;
            # now() would retain the transaction start time across lock waits.
            now = (await security_db.execute(select(func.clock_timestamp()))).scalar_one()
            window_start = now - timedelta(minutes=1)
            criteria = (
                AIRequestEvent.actor_user_account_id == user_id,
                AIRequestEvent.channel == category,
                AIRequestEvent.action == "BUDGET_REQUEST",
                AIRequestEvent.outcome == "ALLOWED",
                AIRequestEvent.created_at > window_start,
            )
            count = (
                await security_db.execute(
                    select(func.count(AIRequestEvent.event_id)).where(*criteria)
                )
            ).scalar_one()

            if count >= limit:
                oldest = (
                    await security_db.execute(
                        select(func.min(AIRequestEvent.created_at)).where(*criteria)
                    )
                ).scalar_one()
                if oldest is not None:
                    retry_after = max(
                        1,
                        math.ceil((oldest + timedelta(minutes=1) - now).total_seconds()),
                    )
                rate_limited = True
                security_db.add(
                    AIRequestEvent(
                        actor_user_account_id=user_id,
                        channel=category,
                        action="BUDGET_REQUEST",
                        scope="NONE",
                        outcome="RATE_LIMITED",
                        request_id=request_id,
                        created_at=now,
                    )
                )
            else:
                security_db.add(
                    AIRequestEvent(
                        actor_user_account_id=user_id,
                        channel=category,
                        action="BUDGET_REQUEST",
                        scope="NONE",
                        outcome="ALLOWED",
                        request_id=request_id,
                        created_at=now,
                    )
                )

            # Release the transaction-scoped advisory lock and durably account
            # for the result before returning/raising to the request handler.
            await security_db.commit()
    except (SQLAlchemyError, TimeoutError) as exc:
        raise _unavailable(exc) from exc

    if rate_limited:
        raise HTTPException(
            status_code=429,
            detail="RATE_LIMITED",
            headers={"Retry-After": str(retry_after)},
        )


async def record_event(
    db: AsyncSession,
    user_id: int,
    channel: str,
    action: str,
    scope: str,
    outcome: str,
    request_id: str,
    command: str | None = None,
) -> None:
    """Persist only structured, allowlisted audit metadata in an isolated txn."""
    del db
    channel = _enum_value(channel)
    action = _enum_value(action)
    scope = _enum_value(scope)
    outcome = _enum_value(outcome)
    _validate_request_id(request_id)
    if (
        channel not in CHANNELS
        or action not in ACTIONS
        or scope not in SCOPES
        or outcome not in OUTCOMES
        or (command is not None and (not isinstance(command, str) or not _COMMAND_PATTERN.fullmatch(command)))
    ):
        raise HTTPException(status_code=422, detail="AI_AUDIT_VALUE_INVALID")

    try:
        async with AsyncSessionLocal() as security_db:
            await _set_transaction_timeouts(security_db)
            security_db.add(
                AIRequestEvent(
                    actor_user_account_id=user_id,
                    channel=channel,
                    action=action,
                    scope=scope,
                    outcome=outcome,
                    command=command,
                    request_id=request_id,
                )
            )
            await security_db.commit()
    except (SQLAlchemyError, TimeoutError) as exc:
        raise _unavailable(exc) from exc


@asynccontextmanager
async def secured_operation(db, user_id: int, channel: str, action: str, scope="NONE", command=None, rate_limit=True):
    """Audit success and failures with the same ID across nested AI/report work."""
    request_id = _request_id.get() or uuid4().hex
    token = _request_id.set(request_id)
    metadata = {"scope": scope, "command": command, "outcome": "SUCCESS", "request_id": request_id}
    try:
        if rate_limit:
            await consume_budget(db, user_id, channel, request_id)
        try:
            yield metadata
        except HTTPException as exc:
            outcome = "DENIED" if exc.status_code in {403, 404} else "REJECTED" if exc.status_code < 500 else "ERROR"
            await record_event(db, user_id, channel, action, metadata["scope"], outcome, request_id, metadata["command"])
            raise
        except Exception:
            await record_event(db, user_id, channel, action, metadata["scope"], "ERROR", request_id, metadata["command"])
            raise
        else:
            await record_event(db, user_id, channel, action, metadata["scope"], metadata["outcome"], request_id, metadata["command"])
    finally:
        _request_id.reset(token)
