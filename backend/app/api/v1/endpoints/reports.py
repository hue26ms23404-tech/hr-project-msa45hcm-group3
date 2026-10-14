import hashlib
import json
import base64
import re
from datetime import date, datetime, timezone, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, or_, and_
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.auth import UserAccount
from app.models.knowledge import ReportRun
from app.schemas.reports import ReportKind, ReportRunCreate, ReportRunCreateResponse, ReportRunRead, ReportRunPreview, ReportRunPage
from app.services.report_service import ReportService, safe_csv, build_xlsx_report
from app.services.report_storage import delete_report, resolve_report_path
from app.services.report_run_service import ReportRunService
from app.services.ai_request_security import secured_operation
from app.services.report_catalog import report_catalog
from app.services.report_access import authorized_run
from app.services.ai_role_scopes import scoped_ai_access
from app.services.ai_access import require_reviewer_role, user_roles

async def require_report_user(user: UserAccount = Depends(get_current_user)):
    require_reviewer_role(user_roles(user))


router = APIRouter(dependencies=[Depends(require_report_user)])


@router.get("/runs/{run_id}/analysis")
async def analyze_report_run(run_id: str, db: AsyncSession = Depends(get_db),
                             user: UserAccount = Depends(get_current_user),
                             _policy: None = Depends(scoped_ai_access)):
    from app.services.ai_analysis_tool import AIAnalysisTool
    result = await AIAnalysisTool.execute(db, user, run_id)
    return JSONResponse(result, headers={"Cache-Control": "private, no-store"})


@router.get("/catalog")
async def get_report_catalog(user: UserAccount = Depends(get_current_user)):
    return JSONResponse(report_catalog(user), headers={"Cache-Control": "private, no-store"})


def _history_cursor(run):
    return base64.urlsafe_b64encode(json.dumps([run.created_at.isoformat(), run.run_id]).encode()).decode()


def _decode_history_cursor(cursor):
    try:
        value = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
        if not isinstance(value, list) or len(value) != 2 or not isinstance(value[1], str) or not re.fullmatch(r"[a-f0-9]{32}", value[1]):
            raise ValueError()
        timestamp = datetime.fromisoformat(value[0])
        if timestamp.tzinfo is None:
            raise ValueError()
        return timestamp, value[1]
    except (ValueError, TypeError, UnicodeError) as exc:
        raise HTTPException(422, "REPORT_CURSOR_INVALID") from exc


async def _authorized_run(db: AsyncSession, user: UserAccount, run_id: str) -> ReportRun:
    return await authorized_run(db, user, run_id)


async def _cleanup_expired_runs(db: AsyncSession, owner_id: int) -> None:
    expired = (await db.scalars(select(ReportRun).where(
        ReportRun.expires_at <= datetime.now(timezone.utc),
        ReportRun.owner_user_account_id == owner_id,
        or_(ReportRun.status.in_(["READY", "GENERATING"]), and_(ReportRun.status == "FAILED", or_(ReportRun.storage_key.is_not(None), ReportRun.snapshot_storage_key.is_not(None)))),
    ).with_for_update())).all()
    if not expired:
        return
    keys = []
    for run in expired:
        keys.extend((run.storage_key, run.snapshot_storage_key))
        run.storage_key = None
        run.snapshot_storage_key = None
        if run.status != "FAILED":
            run.status = "EXPIRED"
    await db.commit()
    for key in keys:
        delete_report(key)


@router.post("/runs", response_model=ReportRunCreateResponse, status_code=201)
async def create_report_run(
    req: ReportRunCreate,
    db: AsyncSession = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
):
    return await ReportRunService.create(db, user, req)


@router.get("/runs", response_model=list[ReportRunRead] | ReportRunPage)
async def list_report_runs(
    response: Response,
    paginated: bool = False,
    cursor: str | None = Query(None, max_length=512),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "private, no-store"
    if cursor:
        _decode_history_cursor(cursor)
    await _cleanup_expired_runs(db, user.user_account_id)
    statement = select(ReportRun).where(
        ReportRun.owner_user_account_id == user.user_account_id,
        ReportRun.created_at >= datetime.now(timezone.utc) - timedelta(days=30),
    )
    if cursor:
        timestamp, last_id = _decode_history_cursor(cursor)
        statement = statement.where(or_(ReportRun.created_at < timestamp, and_(ReportRun.created_at == timestamp, ReportRun.run_id < last_id)))
    rows = list((await db.scalars(statement.order_by(ReportRun.created_at.desc(), ReportRun.run_id.desc()).limit(limit + 1))).all())
    page = rows[:limit]
    if paginated or cursor:
        return {"items": page, "next_cursor": _history_cursor(page[-1]) if len(rows) > limit and page else None}
    return page


@router.get("/runs/{run_id}", response_model=ReportRunPreview)
async def read_report_run(
    run_id: str,
    response: Response,
    offset: int = Query(0, ge=0),
    limit: int | None = Query(None, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "private, no-store"
    run = await _authorized_run(db, user, run_id)
    try:
        snapshot_path = resolve_report_path(run.snapshot_storage_key or "")
        report = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(404, "REPORT_UNAVAILABLE") from exc
    total_rows = len(report["rows"])
    if limit is not None:
        report["rows"] = report["rows"][offset:offset + limit]
    elif offset:
        raise HTTPException(422, "REPORT_PREVIEW_LIMIT_REQUIRED")
    return {"run": run, "report": report, "total_rows": total_rows, "offset": offset, "limit": limit}


@router.get("/runs/{run_id}/download")
@router.get("/runs/{run_id}/file")
async def download_report_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
):
    async with secured_operation(db, user.user_account_id, "REPORT", "REPORT_DOWNLOAD", rate_limit=False) as audit:
        return await _download_report_run(db, user, run_id, audit)


async def _download_report_run(db, user, run_id, audit):
    run = await _authorized_run(db, user, run_id)
    audit["scope"] = run.scope
    audit["command"] = run.kind
    try:
        path = resolve_report_path(run.storage_key or "")
        data = path.read_bytes()
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(404, "REPORT_UNAVAILABLE") from exc
    if hashlib.sha256(data).hexdigest() != run.sha256:
        raise HTTPException(404, "REPORT_UNAVAILABLE")
    filename = f"{run.kind.lower()}_{run.run_id}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store",
    })


@router.get("")
async def generate_report(
    kind: ReportKind,
    start_date: date,
    end_date: date,
    scope: Literal["SELF", "DIRECT_REPORTS", "COMPANY"] | None = None,
    department_id: int | None = Query(None, gt=0),
    output: Literal["json", "csv", "xlsx"] = "json",
    db: AsyncSession = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
):
    if end_date < start_date or start_date.year != end_date.year:
        raise HTTPException(422, "Chọn khoảng ngày hợp lệ trong cùng một năm")
    async with secured_operation(db, user.user_account_id, "REPORT", "REPORT_CREATE", scope=scope or "NONE", command=kind) as audit:
        report = await ReportService.generate(db, user, kind, start_date, end_date, department_id, scope)
        audit["scope"] = report.get("scope", scope or "NONE")
    if output == "xlsx":
        data = build_xlsx_report(report, user.login_email)
        filename = f"{kind.lower()}_{start_date}_{end_date}.xlsx"
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store"},
        )
    if output == "csv":
        fields_by_kind = {
            "ATTENDANCE": ["employee_id", "employee_code", "full_name", "department_id", "recorded_days", "present_days", "incomplete_days", "absent_days", "worked_minutes"],
            "LEAVE": ["employee_id", "employee_code", "full_name", "department_id", "approved_days", "pending_requests", "rejected_requests", "remaining_annual_days"],
            "ATTENDANCE_FIX": ["attendance_fix_id", "employee_id", "employee_code", "full_name", "department_id", "work_date", "event_type", "requested_at", "status", "reason", "created_at"],
            "APPROVAL_QUEUE": ["request_type", "request_id", "employee_id", "employee_code", "full_name", "department_id", "request_date", "detail", "created_at"],
            "HEADCOUNT": ["department_id", "department_name", "employment_status", "employee_count"],
            "MY_PAYSLIP": ["employee_code", "full_name", "period_year", "period_month", "currency_code", "base_salary", "standard_work_days", "actual_work_days", "allowance_amount", "overtime_amount", "deduction_amount", "gross_salary", "insurance_deduction", "taxable_income", "personal_income_tax", "other_deductions", "net_salary"],
            "PAYROLL_SUMMARY": ["department_id", "department_name", "currency_code", "employee_count", "base_salary", "allowance_amount", "overtime_amount", "deduction_amount", "gross_salary", "insurance_deduction", "taxable_income", "personal_income_tax", "other_deductions", "net_salary"],
        }
        fields = fields_by_kind[kind]
        return Response(safe_csv(report["rows"], fields), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{kind.lower()}_{start_date}_{end_date}.csv"', "Cache-Control": "private, no-store"})
    return JSONResponse(content=jsonable_encoder(report), headers={"Cache-Control": "private, no-store"})
