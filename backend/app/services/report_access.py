"""Shared snapshot authorization for preview, download and analysis tools."""
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.models.knowledge import ReportRun
from app.services.report_service import ReportService
from app.services.report_storage import delete_report
from app.services.ai_access import require_reviewer_role, user_roles


async def authorized_run(db, user, run_id):
    require_reviewer_role(user_roles(user))
    run = await db.scalar(select(ReportRun).where(
        ReportRun.run_id == run_id,
        ReportRun.owner_user_account_id == user.user_account_id,
    ))
    if not run or run.status not in {"READY", "EXPIRED"}:
        raise HTTPException(404, "REPORT_UNAVAILABLE")
    expires = run.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if run.status == "EXPIRED" or expires <= datetime.now(timezone.utc):
        run.status = "EXPIRED"
        delete_report(run.storage_key)
        delete_report(run.snapshot_storage_key)
        run.storage_key = None
        run.snapshot_storage_key = None
        await db.commit()
        raise HTTPException(410, "REPORT_EXPIRED")
    try:
        target_filter = {"employee_id": run.filters["employee_id"]} if run.filters.get("employee_id") is not None else {}
        _, current_ids = await ReportService.visible_employee_ids(
            db, user, run.kind, run.scope, run.filters.get("department_id"),
            date.fromisoformat(run.filters["start_date"]), date.fromisoformat(run.filters["end_date"]),
            **target_filter,
        )
    except HTTPException as exc:
        raise HTTPException(404, "REPORT_UNAVAILABLE") from exc
    if not set(run.scope_snapshot).issubset(set(current_ids)):
        raise HTTPException(404, "REPORT_UNAVAILABLE")
    return run
