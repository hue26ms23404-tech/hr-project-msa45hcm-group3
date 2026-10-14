from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ReportKind = Literal["ATTENDANCE", "LEAVE", "ATTENDANCE_FIX", "APPROVAL_QUEUE", "HEADCOUNT", "MY_PAYSLIP", "PAYROLL_SUMMARY"]
ReportScope = Literal["SELF", "DIRECT_REPORTS", "COMPANY"]


class ReportRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ReportKind
    start_date: date
    end_date: date
    scope: ReportScope | None = None
    department_id: int | None = Field(default=None, gt=0)
    employee_id: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def valid_period(self):
        if self.employee_id is not None and self.kind not in {"ATTENDANCE", "LEAVE", "ATTENDANCE_FIX"}:
            raise ValueError("REPORT_EMPLOYEE_FILTER_UNSUPPORTED")
        if self.end_date < self.start_date or self.start_date.year != self.end_date.year:
            raise ValueError("REPORT_PERIOD_INVALID")
        if self.kind in {"MY_PAYSLIP", "PAYROLL_SUMMARY"}:
            from calendar import monthrange
            if self.start_date.day != 1 or self.end_date != self.start_date.replace(day=monthrange(self.start_date.year, self.start_date.month)[1]):
                raise ValueError("PAYROLL_REPORT_REQUIRES_ONE_CALENDAR_MONTH")
        return self


class ReportRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    run_id: str
    kind: str
    scope: str
    status: str
    error_code: str | None = None
    row_count: int | None
    template_version: str
    as_of: datetime | None
    created_at: datetime
    expires_at: datetime


class ReportRunCreateResponse(BaseModel):
    run: ReportRunRead
    report: dict


class ReportRunPreview(BaseModel):
    run: ReportRunRead
    report: dict
    total_rows: int | None = None
    offset: int = 0
    limit: int | None = None


class ReportRunPage(BaseModel):
    items: list[ReportRunRead]
    next_cursor: str | None = None
