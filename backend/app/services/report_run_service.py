"""One snapshot creation path for the reports screen and AI fallback."""

import json
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4, uuid5, NAMESPACE_URL
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select
from app.core.database import AsyncSessionLocal

from app.models.knowledge import ReportRun
from app.services.report_service import ReportService, build_xlsx_report
from app.services.report_storage import delete_report, store_report
from app.services.ai_request_security import secured_operation
from app.services.ai_access import user_roles, require_reviewer_role


class ReportRunService:
    @staticmethod
    def request_filters(req):
        filters = {"start_date": req.start_date.isoformat(), "end_date": req.end_date.isoformat(), "department_id": req.department_id}
        if req.employee_id is not None:
            filters["employee_id"] = req.employee_id
        return filters

    @staticmethod
    async def _save_state(user, req, run_id, scope, employee_ids, status, **values):
        """Persist lifecycle independently from a failed business-query session."""
        async with asyncio.timeout(10):
            async with AsyncSessionLocal() as state_db:
                run = await state_db.scalar(select(ReportRun).where(ReportRun.run_id == run_id, ReportRun.owner_user_account_id == user.user_account_id).with_for_update())
                if run is not None and status == "GENERATING":
                    # A concurrent request cannot reset a completed/active run.
                    raise HTTPException(409, "REPORT_ALREADY_EXISTS")
                if run is None:
                    now = datetime.now(timezone.utc)
                    run = ReportRun(run_id=run_id, owner_user_account_id=user.user_account_id, kind=req.kind, scope=scope, filters=ReportRunService.request_filters(req), template_version=f"{req.kind.lower()}_v1", scope_snapshot=list(employee_ids), as_of=now, expires_at=now + timedelta(hours=1))
                    state_db.add(run)
                run.status = status
                for key, value in values.items():
                    setattr(run, key, value)
                await state_db.flush()
                await state_db.commit()
                return run

    @staticmethod
    async def create(db, user, req, *, source_draft_id=None):
        async with secured_operation(db, user.user_account_id, "REPORT", "REPORT_CREATE", scope=req.scope or "NONE", command=req.kind) as audit:
            result = await ReportRunService._create(db, user, req, source_draft_id=source_draft_id)
            audit["scope"] = result["run"].scope
            return result

    @staticmethod
    async def _create(db, user, req, *, source_draft_id=None):
        # One stable primary key per chat draft makes committed report runs
        # reusable after an uncertain HTTP failure or chat transaction rollback.
        run_id = (uuid5(NAMESPACE_URL, f"hr-ai-report-draft:{source_draft_id}").hex
                  if source_draft_id is not None else uuid4().hex)
        # Resolve the requested scope before replay checks. A draft may have
        # been changed after its original report was committed.
        roles = user_roles(user)
        require_reviewer_role(roles)
        scope = req.scope or ("COMPANY" if req.kind == "PAYROLL_SUMMARY" or req.kind == "HEADCOUNT" and roles & {"HR", "ADMIN"} else "DIRECT_REPORTS" if req.kind in {"HEADCOUNT", "APPROVAL_QUEUE"} else "SELF")
        if source_draft_id is not None:
            async with AsyncSessionLocal() as state_db:
                existing = await state_db.scalar(select(ReportRun).where(
                    ReportRun.run_id == run_id,
                    ReportRun.owner_user_account_id == user.user_account_id))
                if existing is not None:
                    if existing.status == "READY":
                        # Reuse only the exact original operation; never return
                        # an old report for a modified draft or a revoked scope.
                        filters = existing.filters or {}
                        expected = ReportRunService.request_filters(req)
                        if existing.kind != req.kind or filters != expected or existing.scope != scope:
                            raise HTTPException(409, "REPORT_ALREADY_EXISTS")
                        from app.services.report_access import authorized_run
                        verified = await authorized_run(db, user, run_id)
                        return {"run": verified, "report": None}
                    # Do not create another report while the first is still
                    # generating or after an uncertain/failed attempt.
                    raise HTTPException(409, "REPORT_ALREADY_EXISTS")
        employee_ids = []
        started = False
        workbook_key = snapshot_key = None
        try:
            async with asyncio.timeout(30):
                target_filter = {"employee_id": req.employee_id} if req.employee_id is not None else {}
                scope, employee_ids = await ReportService.visible_employee_ids(db, user, req.kind, scope, req.department_id, req.start_date, req.end_date, **target_filter)
                await ReportRunService._save_state(user, req, run_id, scope, employee_ids, "GENERATING")
                started = True
                report = await ReportService.generate(db, user, req.kind, req.start_date, req.end_date, req.department_id, scope, source_employee_ids=employee_ids)
                # The snapshot cannot widen after employee membership changes.
                # Download also checks every frozen source ID against current scope.
                if any(row.get("employee_id") not in employee_ids for row in report["rows"] if "employee_id" in row):
                    raise HTTPException(403, "AI_ACCESS_DENIED")
            report["scope"] = scope
            snapshot = json.dumps(report, ensure_ascii=False, default=lambda value: value.isoformat() if hasattr(value, "isoformat") else str(value)).encode("utf-8")
            workbook = build_xlsx_report(report, user.login_email)
            workbook_key, digest = store_report(workbook, "xlsx")
            snapshot_key, _ = store_report(snapshot, "json")
            run = await ReportRunService._save_state(user, req, run_id, scope, employee_ids, "READY", row_count=len(report["rows"]), storage_key=workbook_key, snapshot_storage_key=snapshot_key, sha256=digest, error_code=None)
        except Exception as exc:
            remaining = []
            for key in (workbook_key, snapshot_key):
                try:
                    delete_report(key)
                    remaining.append(None)
                except OSError:
                    remaining.append(key)  # Keep failed artifacts linked for TTL cleanup.
            if not isinstance(exc, HTTPException) or exc.status_code >= 500:
                try:
                    await ReportRunService._save_state(user, req, run_id, scope, employee_ids, "FAILED", error_code="DATA_SERVICE_UNAVAILABLE", storage_key=remaining[0], snapshot_storage_key=remaining[1], sha256=None, row_count=None)
                except (SQLAlchemyError, TimeoutError, OSError):
                    pass  # A disconnected DB cannot persist failure; never return READY.
            elif started:
                try:
                    await ReportRunService._save_state(user, req, run_id, scope, employee_ids, "FAILED", error_code="REPORT_REQUEST_REJECTED", storage_key=remaining[0], snapshot_storage_key=remaining[1], sha256=None, row_count=None)
                except (SQLAlchemyError, TimeoutError, OSError):
                    pass
            if not isinstance(exc, HTTPException):
                raise HTTPException(status_code=503, detail="DATA_SERVICE_UNAVAILABLE") from exc
            raise
        return {"run": run, "report": report}
